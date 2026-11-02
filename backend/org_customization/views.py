from dataclasses import asdict

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .registry import get_module_registry, get_surface_registry, get_surface_type_registry


class RegistryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "modules": [asdict(spec) for spec in get_module_registry().values()],
                "surface_types": [asdict(spec) for spec in get_surface_type_registry().values()],
                "surfaces": [asdict(spec) for spec in get_surface_registry().values()],
            }
        )
