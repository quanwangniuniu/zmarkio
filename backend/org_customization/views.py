from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Module, Surface, SurfaceType
from .serializers import ModuleSerializer, SurfaceSerializer, SurfaceTypeSerializer


class RegistryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        modules = Module.objects.prefetch_related("route_prefixes")
        surfaces = Surface.objects.select_related(
            "module", "surface_type", "parent__module", "parent__surface_type"
        ).prefetch_related("route_prefixes")
        return Response(
            {
                "modules": ModuleSerializer(modules, many=True).data,
                "surface_types": SurfaceTypeSerializer(SurfaceType.objects.all(), many=True).data,
                "surfaces": SurfaceSerializer(surfaces, many=True).data,
            }
        )
