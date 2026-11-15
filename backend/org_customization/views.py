from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Project, ProjectMember
from core.permissions import can_manage_project_members
from core.slug_mixins import resolve_project_pk

from .serializers import TaskConfigSerializer
from .services import get_task_config, save_task_config


class TaskConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_project(self, request):
        project = Project.objects.filter(pk=resolve_project_pk(request.query_params.get('project_id'))).first()
        if project is None:
            raise NotFound('Project not found.')
        can_edit = can_manage_project_members(request.user, project)
        if not can_edit and not ProjectMember.objects.filter(
            user=request.user, project=project, is_active=True
        ).exists():
            raise PermissionDenied()
        return project, can_edit

    def _response(self, project, can_edit):
        return Response({**get_task_config(project), 'can_edit': can_edit})

    def get(self, request):
        return self._response(*self._get_project(request))

    def put(self, request):
        project, can_edit = self._get_project(request)
        if not can_edit:
            raise PermissionDenied('Project admin access required.')
        if project.organization_id is None:
            raise ValidationError({'project_id': 'Project has no organization.'})
        serializer = TaskConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        save_task_config(project, serializer.validated_data)
        return self._response(project, can_edit)
