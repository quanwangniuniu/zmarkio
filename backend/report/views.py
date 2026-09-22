from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError, NotFound
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from core.models import ProjectMember
from report import kpi_registry
from report.models import CustomKPI, ReportTask, ReportTaskKeyAction
from core.slug_mixins import resolve_pk_for, resolve_project_pk
from task.models import Task
from report.serializers import (
    CustomKPICreateSerializer,
    CustomKPIPreviewSerializer,
    CustomKPISerializer,
    CustomKPIUpdateSerializer,
    ReportTaskSerializer,
    ReportCreateSerializer,
    ReportUpdateSerializer,
    ReportTaskCreateUpdateSerializer,
    ReportTaskKeyActionSerializer,
    ReportKeyActionCreateSerializer,
    ReportKeyActionUpdateSerializer,
)


def _get_accessible_report_queryset(user):
    if not user.is_authenticated:
        return ReportTask.objects.none()
    accessible_project_ids = ProjectMember.objects.filter(
        user=user,
        is_active=True,
    ).values_list("project_id", flat=True)
    return ReportTask.objects.select_related("task", "task__project").filter(
        task__project_id__in=accessible_project_ids
    )


def _get_report_or_404(user, report_id):
    qs = _get_accessible_report_queryset(user)
    try:
        return qs.get(id=report_id)
    except ReportTask.DoesNotExist:
        raise NotFound("Report not found.")


class ReportListCreateView(generics.ListCreateAPIView):
    """
    GET /api/report/reports/
    POST /api/report/reports/
    """

    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method == "POST":
            # Use ReportTaskCreateUpdateSerializer if key_actions is present
            if "key_actions" in self.request.data:
                return ReportTaskCreateUpdateSerializer
            return ReportCreateSerializer
        return ReportTaskSerializer

    def get_queryset(self):
        qs = _get_accessible_report_queryset(self.request.user)
        task_pk = resolve_pk_for(Task, self.request.query_params.get("task"))
        if task_pk:
            qs = qs.filter(task_id=task_pk)
        return qs

    def perform_create(self, serializer):
        task = serializer.validated_data.get("task")
        if task is None:
            raise DRFValidationError({"task": "Task is required for report details."})
        if task.type != "report":
            raise DRFValidationError(
                {"task": 'Report details can only be created for tasks of type "report".'}
            )
        user = self.request.user
        has_membership = ProjectMember.objects.filter(
            user=user,
            project=task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise PermissionDenied("You do not have access to this task.")
        if hasattr(task, "report_task"):
            raise DRFValidationError({"task": "Report details already exist for this task."})

        serializer.save()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        report_task = serializer.instance
        output = ReportTaskSerializer(report_task, context={"request": request}).data
        return Response(output, status=status.HTTP_201_CREATED)


class ReportRetrieveUpdateView(generics.RetrieveUpdateAPIView):
    """
    GET /api/report/reports/{id}/
    PATCH /api/report/reports/{id}/
    """

    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method in {"PATCH", "PUT"}:
            # Use ReportTaskCreateUpdateSerializer if key_actions is present
            if "key_actions" in self.request.data:
                return ReportTaskCreateUpdateSerializer
            return ReportUpdateSerializer
        return ReportTaskSerializer

    def get_queryset(self):
        return _get_accessible_report_queryset(self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        output = ReportTaskSerializer(instance, context={"request": request}).data
        return Response(output)


class ReportKeyActionListCreateView(generics.ListCreateAPIView):
    """
    GET /api/report/reports/{id}/key-actions/
    POST /api/report/reports/{id}/key-actions/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ReportKeyActionCreateSerializer
        return ReportTaskKeyActionSerializer

    def get_queryset(self):
        report_id = self.kwargs.get("id")
        _get_report_or_404(self.request.user, report_id)
        return ReportTaskKeyAction.objects.filter(report_task_id=report_id).order_by(
            "order_index", "id"
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        report_id = self.kwargs.get("id")
        context["report_task"] = _get_report_or_404(self.request.user, report_id)
        return context

    def perform_create(self, serializer):
        report_task = self.get_serializer_context()["report_task"]
        serializer.save(report_task=report_task)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        key_action = serializer.instance
        output = ReportTaskKeyActionSerializer(key_action).data
        return Response(output, status=status.HTTP_201_CREATED)


class ReportKeyActionRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET /api/report/reports/{id}/key-actions/{action_id}/
    PATCH /api/report/reports/{id}/key-actions/{action_id}/
    DELETE /api/report/reports/{id}/key-actions/{action_id}/
    """

    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "action_id"
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method in {"PATCH", "PUT"}:
            return ReportKeyActionUpdateSerializer
        return ReportTaskKeyActionSerializer

    def get_queryset(self):
        report_id = self.kwargs.get("id")
        report = _get_report_or_404(self.request.user, report_id)
        return ReportTaskKeyAction.objects.filter(report_task=report).order_by(
            "order_index", "id"
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        report_id = self.kwargs.get("id")
        context["report_task"] = _get_report_or_404(self.request.user, report_id)
        return context

    def perform_update(self, serializer):
        serializer.save()


# ---------------------------------------------------------------------------
# Custom KPIs
# ---------------------------------------------------------------------------


def _get_accessible_kpi_queryset(user):
    if not user.is_authenticated:
        return CustomKPI.objects.none()
    accessible_project_ids = ProjectMember.objects.filter(
        user=user,
        is_active=True,
    ).values_list("project_id", flat=True)
    return CustomKPI.objects.select_related("project").filter(
        project_id__in=accessible_project_ids
    )


def _requested_date_range(request):
    """Read ?start_date/?end_date, falling back to the registry default window."""
    default_start, default_end = kpi_registry.default_date_range()
    start_date = parse_date(request.query_params.get("start_date") or "") or default_start
    end_date = parse_date(request.query_params.get("end_date") or "") or default_end
    if start_date > end_date:
        raise DRFValidationError(
            {"start_date": "start_date must be on or before end_date."}
        )
    return start_date, end_date


class CustomKPIListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/report/kpis/?project=<slug|id>[&start_date=&end_date=]
    POST /api/report/kpis/
    """

    permission_classes = [IsAuthenticated]
    # A project has a handful of KPIs and the dashboard renders all of them.
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == "POST":
            return CustomKPICreateSerializer
        return CustomKPISerializer

    def _requested_project_pk(self):
        """Resolve `?project=` to a pk the caller is actually a member of.

        Returns `(pk, scoped)`. `scoped` is False when a project was asked for
        but the caller cannot see it, which must not fall back to "no filter".
        Authorization happens here, once, so the queryset and the warehouse
        snapshot can never disagree about which project is in scope -- without
        it, any authenticated user could make the server aggregate another
        project's warehouse data by passing its slug.
        """
        if hasattr(self, "_project_scope"):
            return self._project_scope

        raw = self.request.query_params.get("project")
        if not raw:
            scope = (None, True)
        else:
            project_pk = resolve_project_pk(raw)
            is_member = bool(project_pk) and ProjectMember.objects.filter(
                user=self.request.user,
                project_id=project_pk,
                is_active=True,
            ).exists()
            scope = (project_pk, True) if is_member else (None, False)

        self._project_scope = scope
        return scope

    def get_queryset(self):
        project_pk, scoped = self._requested_project_pk()
        if not scoped:
            return CustomKPI.objects.none()
        qs = _get_accessible_kpi_queryset(self.request.user)
        if project_pk:
            qs = qs.filter(project_id=project_pk)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.method != "GET":
            return context
        # Values are only meaningful for a single project, and resolving them
        # once here keeps a list to one warehouse query.
        project_pk, scoped = self._requested_project_pk()
        if scoped and project_pk:
            start_date, end_date = _requested_date_range(self.request)
            context["metric_snapshot"] = kpi_registry.resolve_metric_values(
                project_pk, start_date, end_date
            )
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kpi = serializer.save()
        output = CustomKPISerializer(kpi, context={"request": request}).data
        return Response(output, status=status.HTTP_201_CREATED)


class CustomKPIDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/report/kpis/{id}/
    PATCH  /api/report/kpis/{id}/
    DELETE /api/report/kpis/{id}/
    """

    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method in {"PATCH", "PUT"}:
            return CustomKPIUpdateSerializer
        return CustomKPISerializer

    def get_queryset(self):
        return _get_accessible_kpi_queryset(self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.method == "GET":
            start_date, end_date = _requested_date_range(self.request)
            context["metric_snapshot"] = kpi_registry.resolve_metric_values(
                self.get_object().project_id, start_date, end_date
            )
        return context

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        instance.refresh_from_db()
        output = CustomKPISerializer(instance, context={"request": request}).data
        return Response(output)


class CustomKPIPreviewView(APIView):
    """
    POST /api/report/kpis/preview/

    Evaluates an unsaved formula. A formula error is part of a 200 response --
    the builder shows it inline while typing rather than treating it as a
    failed request.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CustomKPIPreviewSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        evaluation = kpi_registry.evaluate_for_project(
            data["formula"],
            data["project"].id,
            data.get("start_date"),
            data.get("end_date"),
        )
        return Response(
            {
                "value": str(evaluation.value) if evaluation.ok else None,
                "error": (
                    None
                    if evaluation.ok
                    else {
                        "code": evaluation.error_code,
                        "message": evaluation.error_message,
                    }
                ),
            }
        )


class KPIMetricCatalogView(APIView):
    """
    GET /api/report/kpi-metrics/

    The metric names a formula may reference; drives the editor's autocomplete.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"metrics": kpi_registry.list_metrics()})
