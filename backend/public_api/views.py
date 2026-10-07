"""Public REST API under /api/v1/csm/, for external systems holding an API key or OAuth client."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from public_api.authentication import ApiKeyAuthentication, OAuthClientAuthentication
from public_api.permissions import HasApiScope
from public_api.throttling import ApiCredentialRateThrottle


class PublicApiMixin:
    # No session or JWT: a browser session must never reach these endpoints, and
    # without SessionAuthentication there is no CSRF check to trip over.
    authentication_classes = [ApiKeyAuthentication, OAuthClientAuthentication]
    permission_classes = [IsAuthenticated, HasApiScope]
    throttle_classes = [ApiCredentialRateThrottle]

    @property
    def principal(self):
        return self.request.user


class WhoAmIView(PublicApiMixin, APIView):
    """GET /api/v1/csm/whoami/: the workspace, project and scopes of the calling credential."""

    def get(self, request):
        principal = self.principal
        return Response({
            'credential_type': principal.kind,
            'name': principal.name,
            'organization': {'id': principal.organization_id, 'slug': principal.organization_slug},
            'project_id': principal.project_id,
            'scopes': principal.scopes,
        })
