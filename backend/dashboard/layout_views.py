from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Project
from core.slug_mixins import resolve_project_pk
from .layout import DashboardDocumentSerializer, DashboardLayoutSerializer, layout_response, save_layout
from .models import DashboardLayout


@method_decorator(csrf_exempt, name='dispatch')
class DashboardLayoutView(APIView):
    permission_classes = [IsAuthenticated]

    def project(self, request):
        project_id = resolve_project_pk(request.query_params.get('project_id'))
        if not project_id:
            raise ValidationError({'project_id': 'A valid project is required.'})
        projects = Project.objects.filter(pk=project_id).filter(
            Q(owner=request.user) | Q(members__user=request.user, members__is_active=True)
        ).distinct()
        org_id = getattr(request.user, 'current_organization_id', None) or getattr(request.user, 'organization_id', None)
        if org_id:
            projects = projects.filter(organization_id=org_id)
        project = projects.first()
        if project is None:
            raise NotFound('Project not found.')
        return project

    def get(self, request):
        project = self.project(request)
        layout = DashboardLayout.objects.filter(project=project, user=request.user).first()
        return Response(layout_response(layout.widgets if layout else None))

    def put(self, request):
        project = self.project(request)
        is_document = 'version' in request.data or 'items' in request.data
        serializer = (DashboardDocumentSerializer if is_document else DashboardLayoutSerializer)(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = dict(serializer.validated_data) if is_document else serializer.validated_data['widgets']
        layout = save_layout(project, request.user, value)
        return Response({**layout_response(layout.widgets), 'updated_at': layout.updated_at})
