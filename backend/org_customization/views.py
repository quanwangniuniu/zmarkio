from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.admin_permissions import IsOrgAdmin

from .serializers import TaskConfigSerializer
from .services import get_task_config, get_user_organization, save_task_config


class TaskConfigView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get(self, request):
        return Response(get_task_config(get_user_organization(request.user)))

    def put(self, request):
        serializer = TaskConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = get_user_organization(request.user)
        save_task_config(organization, serializer.validated_data)
        return Response(get_task_config(organization))
