"""Admin console endpoints under /api/external-api/ (JWT, `?project=`), for org and CSM admins."""

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.viewset_mixins import ProjectScopedViewSetMixin
from csm.services.routing_rules import project_organization_id
from external_api.models import ApiKey, OAuthClient, WebhookDelivery, WebhookEndpoint, WebhookEvent
from external_api.permissions import RESOURCES, require_integrations_admin
from external_api.serializers import (
    ApiKeySerializer,
    CredentialWriteSerializer,
    OAuthClientSerializer,
    WebhookDeliverySerializer,
    WebhookEndpointSerializer,
    WebhookEndpointWriteSerializer,
)
from external_api.services import (
    create_api_key,
    create_endpoint,
    create_oauth_client,
    revoke_api_key,
    revoke_oauth_client,
)


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
    serializer_class = ApiKeySerializer
    pagination_class = None

    def get_queryset(self):
        return self.scoped(ApiKey.objects.all())

    def create(self, request, *args, **kwargs):
        serializer = CredentialWriteSerializer(data=request.data)
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
    serializer_class = OAuthClientSerializer
    pagination_class = None

    def get_queryset(self):
        return self.scoped(OAuthClient.objects.all())

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
    serializer_class = WebhookEndpointSerializer
    pagination_class = None
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_queryset(self):
        return self.scoped(WebhookEndpoint.objects.all())

    def create(self, request, *args, **kwargs):
        serializer = WebhookEndpointWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint, secret = create_endpoint(
            organization_id=self.organization_id,
            project_id=self.project_id,
            user=request.user,
            **serializer.validated_data,
        )
        return Response({**WebhookEndpointSerializer(endpoint).data, 'secret': secret}, status=status.HTTP_201_CREATED)


@method_decorator(csrf_exempt, name='dispatch')
class WebhookDeliveryViewSet(IntegrationsAdminMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = WebhookDeliverySerializer

    def get_queryset(self):
        return WebhookDelivery.objects.filter(
            endpoint__organization_id=self.organization_id,
            endpoint__project_id=self.project_id,
        ).order_by('-created_at', '-id')


@method_decorator(csrf_exempt, name='dispatch')
class IntegrationsVocabularyView(IntegrationsAdminMixin, APIView):
    def get(self, request):
        return Response({
            'resources': [{'value': value, 'label': label} for value, label in RESOURCES.items()],
            'events': [{'value': value, 'label': label} for value, label in WebhookEvent.choices],
        })
