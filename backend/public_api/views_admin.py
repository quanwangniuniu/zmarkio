"""
Admin console endpoints under /api/csm/integrations/ (JWT, `?project=`).

Every view is bound to the requested project and its organisation, and only an
org admin or a CSM admin of that organisation gets past `initial()`.
"""

from django.db.models import OuterRef, Subquery
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.viewset_mixins import ProjectScopedViewSetMixin
from csm.services.routing_rules import project_organization_id
from public_api.models import ApiKey, OAuthClient, WebhookDelivery, WebhookEndpoint
from public_api.permissions import require_integrations_admin
from public_api.serializers_admin import (
    ApiKeyCreateSerializer,
    ApiKeySerializer,
    CredentialWriteSerializer,
    OAuthClientSerializer,
    WebhookDeliverySerializer,
    WebhookEndpointSerializer,
    WebhookEndpointWriteSerializer,
    vocabulary_payload,
)
from public_api.services.credentials import (
    create_api_key,
    create_oauth_client,
    revoke_api_key,
    revoke_oauth_client,
)
from public_api.services.webhooks import create_endpoint, redeliver, rotate_secret, send_test


class IntegrationsAdminMixin(ProjectScopedViewSetMixin):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.project_id = self.get_required_project_id()
        # Resolved under the caller's tenant schema, where the project id is unambiguous.
        self.organization_id = project_organization_id(self.project_id)
        require_integrations_admin(request.user, self.organization_id)

    def scoped(self, queryset):
        return queryset.filter(organization_id=self.organization_id, project_id=self.project_id)


@method_decorator(csrf_exempt, name='dispatch')
class ApiKeyViewSet(IntegrationsAdminMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    - GET  /api/csm/integrations/api-keys/?project={id}
    - POST /api/csm/integrations/api-keys/?project={id}              returns the full key once
    - POST /api/csm/integrations/api-keys/{id}/revoke/?project={id}
    """

    serializer_class = ApiKeySerializer

    def get_queryset(self):
        return self.scoped(ApiKey.objects.select_related('created_by'))

    def create(self, request, *args, **kwargs):
        serializer = ApiKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key, raw = create_api_key(
            organization_id=self.organization_id,
            project_id=self.project_id,
            user=request.user,
            **serializer.validated_data,
        )
        return Response({**ApiKeySerializer(key).data, 'key': raw}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        return Response(ApiKeySerializer(revoke_api_key(self.get_object())).data)


@method_decorator(csrf_exempt, name='dispatch')
class OAuthClientViewSet(IntegrationsAdminMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    - GET  /api/csm/integrations/oauth-clients/?project={id}
    - POST /api/csm/integrations/oauth-clients/?project={id}         returns the client secret once
    - POST /api/csm/integrations/oauth-clients/{id}/revoke/?project={id}
    """

    serializer_class = OAuthClientSerializer

    def get_queryset(self):
        return self.scoped(OAuthClient.objects.select_related('created_by'))

    def create(self, request, *args, **kwargs):
        serializer = CredentialWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client, secret = create_oauth_client(
            organization_id=self.organization_id,
            project_id=self.project_id,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(
            {**OAuthClientSerializer(client).data, 'client_secret': secret},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        return Response(OAuthClientSerializer(revoke_oauth_client(self.get_object())).data)


@method_decorator(csrf_exempt, name='dispatch')
class WebhookEndpointViewSet(IntegrationsAdminMixin, viewsets.ModelViewSet):
    """
    - GET/POST          /api/csm/integrations/webhooks/?project={id}     POST returns the signing secret once
    - GET/PATCH/DELETE  /api/csm/integrations/webhooks/{id}/?project={id}
    - POST              /api/csm/integrations/webhooks/{id}/rotate-secret/?project={id}
    - POST              /api/csm/integrations/webhooks/{id}/test/?project={id}   sends a `ping` event
    """

    serializer_class = WebhookEndpointSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        latest = WebhookDelivery.objects.filter(endpoint=OuterRef('pk')).order_by('-created_at', '-id')
        return self.scoped(WebhookEndpoint.objects.select_related('created_by')).annotate(
            last_delivery_status=Subquery(latest.values('status')[:1]),
            last_delivery_at=Subquery(latest.values('created_at')[:1]),
        )

    def _read(self, endpoint):
        return WebhookEndpointSerializer(self.get_queryset().get(pk=endpoint.pk)).data

    def create(self, request, *args, **kwargs):
        serializer = WebhookEndpointWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint, secret = create_endpoint(
            organization_id=self.organization_id,
            project_id=self.project_id,
            user=request.user,
            **serializer.validated_data,
        )
        return Response({**self._read(endpoint), 'secret': secret}, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        endpoint = self.get_object()
        serializer = WebhookEndpointWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(endpoint, field, value)
        endpoint.save(update_fields=[*serializer.validated_data, 'updated_at'])
        return Response(self._read(endpoint))

    @action(detail=True, methods=['post'], url_path='rotate-secret')
    def rotate_secret(self, request, pk=None):
        endpoint = self.get_object()
        return Response({**self._read(endpoint), 'secret': rotate_secret(endpoint)})

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        endpoint = self.get_object()
        if not endpoint.is_active:
            raise ValidationError({'detail': 'Activate the endpoint before sending a test event.'})
        payload = send_test(endpoint)
        return Response({'event_id': payload['id']}, status=status.HTTP_202_ACCEPTED)


@method_decorator(csrf_exempt, name='dispatch')
class WebhookDeliveryViewSet(IntegrationsAdminMixin, viewsets.ReadOnlyModelViewSet):
    """
    The delivery log: one row per attempt, newest first.

    - GET  /api/csm/integrations/webhook-deliveries/?project={id}[&endpoint=&event_type=&status=]
    - POST /api/csm/integrations/webhook-deliveries/{id}/redeliver/?project={id}
    """

    serializer_class = WebhookDeliverySerializer

    def get_queryset(self):
        qs = WebhookDelivery.objects.filter(
            endpoint__organization_id=self.organization_id,
            endpoint__project_id=self.project_id,
        )
        params = self.request.query_params
        for param, lookup in (('endpoint', 'endpoint_id'), ('event_type', 'event_type'), ('status', 'status')):
            if params.get(param):
                qs = qs.filter(**{lookup: params[param]})
        return qs.order_by('-created_at', '-id')

    @action(detail=True, methods=['post'])
    def redeliver(self, request, pk=None):
        delivery = self.get_object()
        if not WebhookEndpoint.objects.filter(pk=delivery.endpoint_id, is_active=True).exists():
            raise ValidationError({'detail': 'The endpoint is inactive.'})
        redeliver(delivery)
        return Response({'event_id': str(delivery.event_id)}, status=status.HTTP_202_ACCEPTED)


@method_decorator(csrf_exempt, name='dispatch')
class IntegrationsVocabularyView(IntegrationsAdminMixin, APIView):
    """GET /api/csm/integrations/vocabulary/?project={id}: scopes and webhook events the UI offers."""

    def get(self, request):
        return Response(vocabulary_payload())
