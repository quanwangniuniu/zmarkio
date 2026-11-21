from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.utils import record_audit_entry
from core.admin_utils import is_org_admin
from core.models import Project, ProjectMember
from core.permissions import is_project_owner
from core.slug_mixins import resolve_project_pk

from .serializers import SystemConfigSerializer, TaskConfigSerializer
from .services import config_changes, get_system_config, save_system_config, save_task_config


def can_edit_project_config(user, project):
    if is_project_owner(user, project):
        return True
    return is_org_admin(user) and project.organization_id is not None and project.organization_id == user.organization_id


class ProjectConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_project(self, request):
        project = Project.objects.filter(pk=resolve_project_pk(request.query_params.get('project_id'))).first()
        if project is None:
            raise NotFound('Project not found.')
        can_edit = can_edit_project_config(request.user, project)
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
            raise PermissionDenied('Organization admin or project owner access required.')
        if project.organization_id is None:
            raise ValidationError({'project_id': 'Project has no organization.'})
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            before = self.get_config(project, cached=False)
            self.save_config(project, data)
            before, after = config_changes(before, self.get_config(project, cached=False))
            record_audit_entry(
                actor=request.user,
                action='project.config_updated',
                target=project,
                before=before,
                after=after,
                organization=project.organization,
                project=project,
            )
        return self._response(project, can_edit)


class TaskConfigView(ProjectConfigView):
    serializer_class = TaskConfigSerializer

    def get_config(self, project, cached=True):
        return get_system_config(project, cached)['task']

    def save_config(self, project, data):
        save_task_config(project, data)


class OptionConfigView(ProjectConfigView):
    http_method_names = ['get', 'head', 'options']
    module = None

    def get_config(self, project, cached=True):
        return get_system_config(project, cached)[self.module]


class SystemConfigView(ProjectConfigView):
    serializer_class = SystemConfigSerializer

    def get_config(self, project, cached=True):
        return get_system_config(project, cached)

    def save_config(self, project, data):
        save_system_config(project, data)
