from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Project, ProjectMember
from core.permissions import can_manage_project_members
from core.slug_mixins import resolve_project_pk

from .serializers import SystemConfigSerializer, TaskConfigSerializer
from .services import get_option_config, get_system_config, get_task_config, save_system_config, save_task_config


class ProjectConfigView(APIView):
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
        return Response({**self.get_config(project), 'can_edit': can_edit})

    def get(self, request):
        return self._response(*self._get_project(request))

    def put(self, request):
        project, can_edit = self._get_project(request)
        if not can_edit:
            raise PermissionDenied('Project admin access required.')
        if project.organization_id is None:
            raise ValidationError({'project_id': 'Project has no organization.'})
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.save_config(project, serializer.validated_data)
        return self._response(project, can_edit)


class TaskConfigView(ProjectConfigView):
    serializer_class = TaskConfigSerializer

    def get_config(self, project):
        return get_task_config(project)

    def save_config(self, project, data):
        save_task_config(project, data)


class OptionConfigView(ProjectConfigView):
    http_method_names = ['get', 'head', 'options']
    module = None

    def get_config(self, project):
        return get_option_config(project, self.module)


class SystemConfigView(ProjectConfigView):
    serializer_class = SystemConfigSerializer

    def get_config(self, project):
        return get_system_config(project)

    def save_config(self, project, data):
        save_system_config(project, data)
