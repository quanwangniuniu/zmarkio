"""
Admin console endpoints under /api/csm/integrations/ (JWT, `?project=`).

Every view is bound to the requested project and its organisation, and only an
org admin or a CSM admin of that organisation gets past `initial()`.
"""

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.viewset_mixins import ProjectScopedViewSetMixin
from csm.services.routing_rules import project_organization_id
from public_api.models import ApiKey, OAuthClient
from public_api.permissions import require_integrations_admin
from public_api.serializers_admin import (
    ApiKeyCreateSerializer,
    ApiKeySerializer,
    CredentialWriteSerializer,
    OAuthClientSerializer,
    vocabulary_payload,
)
from public_api.services.credentials import (
    create_api_key,
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
class IntegrationsVocabularyView(IntegrationsAdminMixin, APIView):
    """GET /api/csm/integrations/vocabulary/?project={id}: scopes the UI offers."""

    def get(self, request):
        return Response(vocabulary_payload())
