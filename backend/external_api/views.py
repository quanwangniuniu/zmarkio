"""External API: CRUD for the eight CSM resources, the admin console, and routes.

Each resource subclasses the internal viewset and replaces only what a
credential cannot do: authentication, the queryset, and writes that expect a
person. Extra actions (claim, invite, reorder) are not exposed.

Authentication runs at the view, after TenantSchemaMiddleware. That middleware
only understands user JWTs, so the request is still on the public schema, where
credentials live. A valid credential then switches search_path to its
organisation, and the middleware resets it after the response.
"""

import hashlib
import hmac
import secrets

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.signing import BadSignature, dumps, loads
from django.db import connection
from django.urls import include, path
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from psycopg2 import sql
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle
from rest_framework.views import APIView

from chat.services import UnsafeUrlError
from core.admin_utils import get_org_admin_org_ids
from core.crypto import encrypt_token
from core.models import Organization, Project
from core.services.tenant import slug_to_schema_name
from core.viewset_mixins import ProjectScopedViewSetMixin
from csm.models import (
    Conversation, CustomerUser, Queue, QuickReplyTemplate, QuickReplyTemplateHistory, RoutingRule, Ticket,
)
from csm.serializers import ConversationSerializer, RoutingRuleSerializer
from csm.services.routing_rules import create_rule, project_organization_id
from csm.views import (
    ConversationViewSet, CustomerUserViewSet, QueueViewSet, QuickReplyTemplateViewSet, RoutingRuleViewSet,
    TicketViewSet,
)
from customer.models import Customer, CustomerOrganisation
from customer.views import CustomerOrganisationViewSet, CustomerViewSet
from experience_group.models import ExperienceGroup
from external_api.enums import CredentialKind, WebhookEvent
from external_api.models import Credential, WebhookDelivery, WebhookEndpoint
from external_api.tasks import validate_webhook_url

User = get_user_model()

KEY_PREFIX = 'zmk'
BEARER_SALT = 'external-api-bearer'
BEARER_MAX_AGE_SECONDS = 3600
MAX_CREDENTIALS_PER_PROJECT = 10
MAX_WEBHOOKS_PER_PROJECT = 10
_DUMMY_HASH = hashlib.sha256(b'missing').hexdigest()
RESOURCES = (
    'customers', 'organisations', 'tickets', 'conversations', 'templates', 'routing_rules', 'queues', 'agents',
)
SCOPES = tuple(f'{resource}:{access}' for resource in RESOURCES for access in ('read', 'write'))


# --- Authentication ---------------------------------------------------------

class ApiPrincipal:
    """request.user for a credential. Deliberately not a User, so no person's role applies."""

    is_authenticated = True
    is_anonymous = False
    is_staff = False
    is_superuser = False
    pk = None
    id = None

    def __init__(self, credential):
        self.organization_id = credential.organization_id
        self.project_id = credential.project_id
        self.scopes = list(credential.scopes)


def hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def find_credential(public_id, kind, secret=None):
    """The usable credential, or None. With `secret`, it must match the stored hash."""
    credential = Credential.objects.select_related('organization').filter(
        public_id=public_id, kind=kind, is_deleted=False, organization__is_active=True,
    ).first()
    if secret is not None:
        stored = credential.secret_hash if credential else _DUMMY_HASH
        if not hmac.compare_digest(stored, hash_secret(secret)):
            return None
    return credential


def credential_for_api_key(raw):
    prefix, _, rest = raw.partition('_')
    public_id, _, secret = rest.partition('_')
    if prefix != KEY_PREFIX or not public_id or not secret:
        return None
    return find_credential(public_id, CredentialKind.API_KEY, secret)


def credential_for_bearer(token):
    try:
        public_id = loads(token, salt=BEARER_SALT, max_age=BEARER_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    return find_credential(public_id, CredentialKind.CLIENT)


class CredentialAuthentication(BaseAuthentication):
    """An API key in X-API-Key, or a client bearer token in Authorization."""

    def authenticate(self, request):
        api_key = request.META.get('HTTP_X_API_KEY', '').strip()
        scheme, _, token = request.META.get('HTTP_AUTHORIZATION', '').partition(' ')
        if api_key:
            credential = credential_for_api_key(api_key)
        elif scheme.lower() == 'bearer' and token.strip():
            credential = credential_for_bearer(token.strip())
        else:
            return None
        if credential is None:
            raise AuthenticationFailed('Invalid or expired credential.')
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL('SET search_path TO {}, public').format(
                    sql.Identifier(slug_to_schema_name(credential.organization.slug)),
                ))
        return ApiPrincipal(credential), credential

    def authenticate_header(self, request):
        # A missing or bad credential answers 401, not 403.
        return 'Bearer realm="api"'


class CredentialAccess(BasePermission):
    """Reads need `<resource>:read` or `:write`. Everything else needs `<resource>:write`."""

    message = 'This credential does not have the scope this request needs.'

    def has_permission(self, request, view):
        principal = request.user
        if not isinstance(principal, ApiPrincipal):
            return False
        if f'{view.api_resource}:write' in principal.scopes:
            return True
        return request.method in SAFE_METHODS and f'{view.api_resource}:read' in principal.scopes


class CredentialRateThrottle(SimpleRateThrottle):
    """Counts requests per credential.

    ApiPrincipal has no pk, so the built-in throttles cannot tell credentials apart.
    """

    scope = 'external_api'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': request.auth.public_id}


# --- Scoping ----------------------------------------------------------------
# Project ids repeat across organisation schemas, so every queryset filters by
# the credential's organisation as well as its project.

def scoped_customer_organisations(principal):
    return CustomerOrganisation.objects.filter(organization_id=principal.organization_id)


def scoped_queues(principal):
    return Queue.objects.filter(
        project_id=principal.project_id,
        organisation__organization_id=principal.organization_id,
        is_active=True,
    )


def scoped_tickets(principal):
    return Ticket.objects.filter(queue__in=scoped_queues(principal)).select_related(
        'queue', 'assigned_to', 'conversation',
    )


def scoped_conversations(principal):
    return Conversation.objects.filter(queue__in=scoped_queues(principal)).select_related(
        'customer', 'queue', 'assigned_to__user',
    )


def scoped_customers(principal):
    return Customer.objects.filter(
        project_id=principal.project_id,
        organisation__organization_id=principal.organization_id,
    )


def scoped_template_organisations(principal):
    """Customer organisations that have an active queue in this project."""
    return scoped_customer_organisations(principal).filter(id__in=scoped_queues(principal).values('organisation_id'))


def scoped_templates(principal):
    return QuickReplyTemplate.objects.filter(
        organisation__in=scoped_template_organisations(principal), is_active=True,
    )


def scoped_routing_rules(principal):
    return RoutingRule.objects.filter(organization_id=principal.organization_id, project_id=principal.project_id)


def scoped_agents(principal):
    return CustomerUser.objects.filter(queue__in=scoped_queues(principal), is_active=True)


def scoped_experience_groups(principal):
    # Same check the internal routing-rule API makes.
    return ExperienceGroup.objects.filter(project_id=principal.project_id)


def scoped_workspace(principal):
    return Organization.objects.filter(pk=principal.organization_id)


def scoped_project(principal):
    return Project.objects.filter(pk=principal.project_id, organization_id=principal.organization_id)


def scoped_assignable_users(principal):
    """People with an active profile on a queue in this project."""
    return User.objects.filter(customer_user_profiles__in=scoped_agents(principal)).distinct()


# --- Resources --------------------------------------------------------------

class ExternalResourceMixin:
    authentication_classes = [CredentialAuthentication]
    permission_classes = [IsAuthenticated, CredentialAccess]
    throttle_classes = [CredentialRateThrottle]
    filter_backends = []
    api_resource = None
    rows = None
    relations = {}
    hidden_fields = ()
    # Fields that only store a project id. Project ids repeat per workspace, so
    # a credential must not write them.
    rejected_writes = ()

    @classmethod
    def get_extra_actions(cls):
        return []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in SAFE_METHODS:
            present = [name for name in self.rejected_writes if name in request.data]
            if present:
                raise ValidationError({name: 'This field cannot be set through the external API.' for name in present})

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        return self.rows(self.request.user)

    def get_object(self):
        # By id. The internal queue and template views look up by slug.
        return GenericAPIView.get_object(self)

    def get_required_project_id(self):
        return self.request.user.project_id

    def get_serializer_context(self):
        # No request: several internal serializers treat request.user as a person.
        # The scoped relation querysets below do that check instead.
        return {'request': None, 'format': self.format_kwarg, 'view': self, 'project_id': self.request.user.project_id}

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        fields = getattr(serializer, 'child', serializer).fields
        for name, scope in self.relations.items():
            field = fields.get(name)
            if field is not None and not field.read_only and hasattr(field, 'queryset'):
                field.queryset = scope(self.request.user)
        for name in self.hidden_fields:
            fields.pop(name, None)
        return serializer


class CustomerResource(ExternalResourceMixin, CustomerViewSet):
    api_resource = 'customers'
    rows = staticmethod(scoped_customers)
    rejected_writes = ('experience_group', 'region', 'status_label')
    relations = {'organisation': scoped_customer_organisations}


class CustomerOrganisationResource(ExternalResourceMixin, CustomerOrganisationViewSet):
    api_resource = 'organisations'
    rows = staticmethod(scoped_customer_organisations)
    rejected_writes = ('region',)
    relations = {'organization': scoped_workspace}
    hidden_fields = ('customers',)

    def create(self, request, *args, **kwargs):
        # The internal create makes the caller its CSM admin. A credential is not a person.
        serializer = self.get_serializer(data={**request.data, 'organization': request.user.organization_id})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        # The internal destroy requires the caller to be a CSM admin.
        instance = self.get_object()
        count = instance.customers.count()
        if count:
            raise ValidationError({
                'detail': f'Cannot delete: {count} customer(s) belong to this organisation. Reassign them first.',
            })
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TicketResource(ExternalResourceMixin, TicketViewSet):
    api_resource = 'tickets'
    rows = staticmethod(scoped_tickets)
    relations = {'queue': scoped_queues, 'conversation': scoped_conversations, 'assigned_to': scoped_assignable_users}


class ConversationResource(ExternalResourceMixin, ConversationViewSet):
    api_resource = 'conversations'
    rows = staticmethod(scoped_conversations)
    relations = {'queue': scoped_queues, 'customer': scoped_customers, 'assigned_to': scoped_agents}

    def get_serializer_class(self):
        # The internal retrieve serializer includes the full transcript.
        return ConversationSerializer

    def perform_create(self, serializer):
        if not serializer.validated_data.get('queue'):
            raise ValidationError({'queue': 'This field is required.'})
        serializer.save()


class TemplateResource(ExternalResourceMixin, QuickReplyTemplateViewSet):
    api_resource = 'templates'
    rows = staticmethod(scoped_templates)
    rejected_writes = ('is_active',)
    relations = {'organisation': scoped_template_organisations}

    # The internal create and update record request.user, which is not a person here.
    def perform_create(self, serializer):
        serializer.save(created_by=None)

    def perform_update(self, serializer):
        instance = serializer.instance
        QuickReplyTemplateHistory.objects.create(
            template=instance,
            edited_by=None,
            title=instance.title,
            content=instance.content,
            rich_body=instance.rich_body,
            tags=instance.tags,
        )
        serializer.save()


class QueueResource(ExternalResourceMixin, QueueViewSet):
    api_resource = 'queues'
    rows = staticmethod(scoped_queues)
    rejected_writes = ('sla_policy', 'is_active')
    relations = {'organisation': scoped_customer_organisations, 'project': scoped_project}

    def perform_create(self, serializer):
        if not serializer.validated_data.get('organisation'):
            raise ValidationError({'organisation': 'This field is required.'})
        serializer.save(project_id=self.request.user.project_id)


class AgentResource(ExternalResourceMixin, CustomerUserViewSet):
    api_resource = 'agents'
    rows = staticmethod(scoped_agents)
    relations = {'organisation': scoped_customer_organisations, 'queue': scoped_queues}

    def perform_create(self, serializer):
        # The internal create signs up unknown emails. An integration may only
        # give an agent profile to someone who is already in this workspace.
        serializer.validated_data.pop('team', None)
        serializer.validated_data['user_type'] = 'agent'
        email = serializer.validated_data.pop('email', '')
        user = User.objects.filter(
            email__iexact=email,
            organization_memberships__organization_id=self.request.user.organization_id,
            organization_memberships__is_active=True,
        ).first()
        if user is None:
            raise ValidationError({'email': 'No active member of this workspace has that email.'})
        if CustomerUser.objects.filter(user=user, queue=serializer.validated_data.get('queue')).exists():
            raise ValidationError({'email': 'This person already has a profile for that queue.'})
        serializer.save(user=user)

    def perform_update(self, serializer):
        serializer.validated_data.pop('team', None)
        serializer.validated_data.pop('user_type', None)
        serializer.save()


class RoutingRuleResource(ExternalResourceMixin, RoutingRuleViewSet):
    api_resource = 'routing_rules'
    rows = staticmethod(scoped_routing_rules)
    relations = {'experience_group': scoped_experience_groups, 'target_queue': scoped_queues}

    def create(self, request, *args, **kwargs):
        # The internal create records request.user, which is not a person here.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            rule = create_rule(
                request.user.project_id,
                user=None,
                experience_group=data['experience_group'],
                name=data['name'],
                target_queue=data['target_queue'],
                conditions=data.get('conditions', []),
                match_mode=data.get('match_mode', RoutingRule.MatchMode.ALL),
                is_enabled=data.get('is_enabled', True),
                add_tags=data.get('add_tags', []),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)
        return Response(RoutingRuleSerializer(rule).data, status=status.HTTP_201_CREATED)


# --- Admin console (JWT) ----------------------------------------------------

def _credential_row(credential):
    return {
        'id': credential.id,
        'name': credential.name,
        'kind': credential.kind,
        'public_id': credential.public_id,
        'scopes': credential.scopes,
        'created_at': credential.created_at,
    }


def _webhook_row(endpoint):
    return {'id': endpoint.id, 'url': endpoint.url, 'events': endpoint.events, 'created_at': endpoint.created_at}


@method_decorator(csrf_exempt, name='dispatch')
class IntegrationAdminView(ProjectScopedViewSetMixin, APIView):
    """Organisation admins manage one project's credentials and webhooks. Secrets are returned only on create."""

    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.project_id = self.get_required_project_id()
        self.organization_id = project_organization_id(self.project_id)
        if not (request.user.is_superuser or self.organization_id in get_org_admin_org_ids(request.user)):
            raise PermissionDenied('Only an organisation admin can manage API access and webhooks.')

    def credentials(self):
        return Credential.objects.filter(
            organization_id=self.organization_id, project_id=self.project_id, is_deleted=False,
        )

    def endpoints(self):
        return WebhookEndpoint.objects.filter(
            organization_id=self.organization_id, project_id=self.project_id, is_deleted=False,
        )


class IntegrationView(IntegrationAdminView):
    def get(self, request):
        deliveries = WebhookDelivery.objects.filter(endpoint__in=self.endpoints()).select_related('endpoint')[:50]
        return Response({
            'resources': list(RESOURCES),
            'events': [{'value': value, 'label': label} for value, label in WebhookEvent.choices],
            'credentials': [_credential_row(row) for row in self.credentials()],
            'webhooks': [_webhook_row(row) for row in self.endpoints()],
            'deliveries': [
                {
                    'id': row.id,
                    'url': row.endpoint.url,
                    'event_type': row.event_type,
                    'attempt': row.attempt,
                    'response_code': row.response_code,
                    'created_at': row.created_at,
                }
                for row in deliveries
            ],
        })


class CredentialView(IntegrationAdminView):
    def post(self, request):
        name = str(request.data.get('name') or '').strip()
        kind = request.data.get('kind')
        scopes = request.data.get('scopes') or []
        if not name:
            raise ValidationError({'name': 'This field is required.'})
        if kind not in CredentialKind.values:
            raise ValidationError({'kind': 'Choose api_key or client.'})
        if not isinstance(scopes, list) or any(scope not in SCOPES for scope in scopes):
            raise ValidationError({'scopes': 'Unknown scope.'})
        if self.credentials().count() >= MAX_CREDENTIALS_PER_PROJECT:
            return Response(
                {'detail': f'A project can have at most {MAX_CREDENTIALS_PER_PROJECT} credentials.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        public_id, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
        credential = Credential.objects.create(
            organization_id=self.organization_id,
            project_id=self.project_id,
            name=name,
            kind=kind,
            public_id=public_id,
            secret_hash=hash_secret(secret),
            scopes=scopes,
        )
        revealed = f'{KEY_PREFIX}_{public_id}_{secret}' if kind == CredentialKind.API_KEY else secret
        return Response({**_credential_row(credential), 'secret': revealed}, status=status.HTTP_201_CREATED)

    def delete(self, request, pk):
        # Revoke. Authentication already refuses deleted credentials.
        updated = self.credentials().filter(pk=pk).update(is_deleted=True)
        return Response(status=status.HTTP_204_NO_CONTENT if updated else status.HTTP_404_NOT_FOUND)


class WebhookView(IntegrationAdminView):
    def post(self, request):
        events = request.data.get('events') or []
        if not isinstance(events, list) or not events or any(event not in WebhookEvent.values for event in events):
            raise ValidationError({'events': 'Choose one or more supported events.'})
        if self.endpoints().count() >= MAX_WEBHOOKS_PER_PROJECT:
            return Response(
                {'detail': f'A project can have at most {MAX_WEBHOOKS_PER_PROJECT} webhooks.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            url, _address = validate_webhook_url(str(request.data.get('url') or '').strip())
        except UnsafeUrlError as exc:
            raise ValidationError({'url': str(exc)})
        secret = f'whsec_{secrets.token_urlsafe(32)}'
        endpoint = WebhookEndpoint.objects.create(
            organization_id=self.organization_id,
            project_id=self.project_id,
            url=url,
            events=events,
            secret_encrypted=encrypt_token(secret),
        )
        return Response({**_webhook_row(endpoint), 'secret': secret}, status=status.HTTP_201_CREATED)

    def delete(self, request, pk):
        updated = self.endpoints().filter(pk=pk).update(is_deleted=True)
        return Response(status=status.HTTP_204_NO_CONTENT if updated else status.HTTP_404_NOT_FOUND)


@method_decorator(csrf_exempt, name='dispatch')
class ClientTokenView(APIView):
    """OAuth 2.0 client-credentials grant: exchange a client id and secret for a bearer token."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'external_api_token'

    def post(self, request):
        if request.data.get('grant_type') != 'client_credentials':
            return Response({'error': 'unsupported_grant_type'}, status=status.HTTP_400_BAD_REQUEST)
        client_id, client_secret = request.data.get('client_id'), request.data.get('client_secret')
        credential = None
        if isinstance(client_id, str) and isinstance(client_secret, str):
            credential = find_credential(client_id, CredentialKind.CLIENT, client_secret)
        if credential is None:
            return Response({'error': 'invalid_client'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({
            'access_token': dumps(credential.public_id, salt=BEARER_SALT),
            'token_type': 'Bearer',
            'expires_in': BEARER_MAX_AGE_SECONDS,
        })


# --- Routes -----------------------------------------------------------------
# Mounted at the site root by backend/urls.py, so these stay apart from the
# internal /api/csm/ routes.

router = SimpleRouter()
router.register(r'customers', CustomerResource, basename='external-api-customer')
router.register(r'organisations', CustomerOrganisationResource, basename='external-api-organisation')
router.register(r'tickets', TicketResource, basename='external-api-ticket')
router.register(r'conversations', ConversationResource, basename='external-api-conversation')
router.register(r'templates', TemplateResource, basename='external-api-template')
router.register(r'routing-rules', RoutingRuleResource, basename='external-api-routing-rule')
router.register(r'queues', QueueResource, basename='external-api-queue')
router.register(r'agents', AgentResource, basename='external-api-agent')

urlpatterns = [
    path('api/v1/csm/', include(router.urls)),
    path('api/v1/oauth/token/', ClientTokenView.as_view(), name='external-api-token'),
    path('api/external-api/', IntegrationView.as_view(), name='external-api-admin'),
    path('api/external-api/credentials/', CredentialView.as_view(), name='external-api-credentials'),
    path('api/external-api/credentials/<int:pk>/', CredentialView.as_view(), name='external-api-credential'),
    path('api/external-api/webhooks/', WebhookView.as_view(), name='external-api-webhooks'),
    path('api/external-api/webhooks/<int:pk>/', WebhookView.as_view(), name='external-api-webhook'),
]
