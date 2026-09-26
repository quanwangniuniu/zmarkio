"""
Read-only projection of the in-code module / surface registries.

Contract endpoint only (MED-453): no tables, no business logic. The
effective per-org config API lands with B4 under the same prefix.
"""
from dataclasses import asdict

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from org_customization.registry import get_module_registry, get_surface_registry


class RegistryView(APIView):
    """GET /api/org-customization/registry/ — the frozen module/surface contract."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "modules": [asdict(spec) for spec in get_module_registry().values()],
                "surfaces": [asdict(spec) for spec in get_surface_registry().values()],
            }
        )
