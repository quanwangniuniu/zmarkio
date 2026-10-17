from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from django_filters.rest_framework import DjangoFilterBackend
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from core.slug_mixins import resolve_pk_for
from task.models import Task
from .services import ExperimentService, PacingService, RollbackHistoryService
from .models import (
    OptimizationExperiment,
    ExperimentMetric,
    ScalingAction,
    RollbackHistory,
    ScalingPlan,
    ScalingStep,
    Optimization,
)
from core.models import ProjectMember

User = get_user_model()
from .serializers import (
    OptimizationExperimentSerializer,
    ExperimentMetricSerializer,
    MetricIngestSerializer,
    ScalingActionSerializer,
    ScalingActionRollbackSerializer,
    RollbackHistorySerializer,
    ScalingPlanSerializer,
    ScalingStepSerializer,
    OptimizationSerializer,
    CampaignPacingForecastSerializer,
)


# ==================== EXPERIMENT VIEWS ====================

class ExperimentListCreateView(generics.ListCreateAPIView):
    """
    GET /optimization/experiments/
    POST /optimization/experiments/
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['status', 'experiment_type']
    
    def get_queryset(self):
        """Return the current user's experiments with custom date filtering."""
        queryset = OptimizationExperiment.objects.filter(
            created_by=self.request.user
        ).order_by('-id')
        
        # Filter experiments by start and end date
        start_before = self.request.query_params.get('start_before')
        end_after = self.request.query_params.get('end_after')
        
        if start_before:
            queryset = queryset.filter(start_date__lt=start_before)
        if end_after:
            queryset = queryset.filter(end_date__gt=end_after)
        
        return queryset
    
    def get_serializer_class(self):
        """Use unified serializer for all methods"""
        return OptimizationExperimentSerializer
    
    def create(self, request, *args, **kwargs):
        """Override create to set created_by field"""
        serializer = self.get_serializer(data=request.data)
        
        if serializer.is_valid():
            # Create experiment with validated data
            experiment = serializer.save(created_by=request.user)
            task = getattr(experiment, 'task', None)
            if task:
                task.link_to_object(experiment)
                task.save()

            return Response(
                OptimizationExperimentSerializer(experiment).data,
                status=status.HTTP_201_CREATED
            )
        
        # Serializer validation errors
        return Response(
            {'detail': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    

class ExperimentUpdateView(generics.RetrieveUpdateAPIView):
    """
    PATCH /optimization/experiments/{id}/
    """
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'
    
    def get_queryset(self):
        """Return the current user's experiments only (scoped by created_by)."""
        return OptimizationExperiment.objects.filter(created_by=self.request.user)
    
    def get_serializer_class(self):
        """Use unified serializer for all requests"""
        return OptimizationExperimentSerializer
    
    def update(self, request, *args, **kwargs):
        """Override update for partial updates"""
        instance = self.get_object()
        
        # Check status transition business rules if status is being updated
        if 'status' in request.data:
            new_status = request.data['status']
            current_status = instance.status
            
            is_valid, error_message = ExperimentService.validate_experiment_status_transition(
                current_status, new_status
            )
            
            if not is_valid:
                return Response(
                    {'detail': {'status': error_message}},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        
        if serializer.is_valid():
            # Update experiment with validated data
            updated_experiment = serializer.save()
            
            return Response(
                OptimizationExperimentSerializer(updated_experiment).data,
                status=status.HTTP_200_OK
            )
        
        # Serializer validation errors
        return Response(
            {'detail': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    

# ==================== METRIC VIEWS ====================

class ExperimentMetricsView(generics.ListAPIView):
    """
    GET /optimization/experiments/{id}/metrics/
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ExperimentMetricSerializer
    
    def get_queryset(self):
        """Return metrics for the experiment"""
        experiment_id = self.kwargs['id']
        experiment = ExperimentService.check_experiment_exists(experiment_id)
        if experiment is None:
            return Response(
                {'detail': 'Experiment not found'},
                status=status.HTTP_400_BAD_REQUEST
            )
        return ExperimentMetric.objects.filter(
            experiment_id=experiment
        ).order_by('-recorded_at')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ingest_experiment_metrics(request, id):
    """
    POST /optimization/experiments/{id}/metrics/ingest/
    """
    # Verify experiment exists using service layer
    experiment = ExperimentService.check_experiment_exists(id)
    if experiment is None:
        return Response(
            {'detail': 'Experiment not found'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Validate and create metric
    serializer = MetricIngestSerializer(data=request.data)
    if serializer.is_valid():
        metric = ExperimentMetric.objects.create(
            experiment_id=experiment,
            metric_name=serializer.validated_data['metric_name'],
            metric_value=serializer.validated_data['metric_value']
        )
        
        return Response(
            ExperimentMetricSerializer(metric).data,
            status=status.HTTP_201_CREATED
        )
    
    return Response(
        {'detail': serializer.errors},
        status=status.HTTP_400_BAD_REQUEST
    )


# ==================== SCALING ACTION VIEWS ====================

class ScalingActionListCreateView(generics.ListCreateAPIView):
    """
    GET /optimization/scaling/
    POST /optimization/scaling/
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['action_type', 'campaign_id']
    
    def get_queryset(self):
        """Return the current user's scaling actions with custom date filtering."""
        queryset = ScalingAction.objects.filter(
            performed_by=self.request.user
        ).order_by('-performed_at')
        
        # Custom date filtering as specified in OpenAPI spec
        performed_before = self.request.query_params.get('performed_before')
        
        if performed_before:
            queryset = queryset.filter(performed_at__lt=performed_before)
        
        return queryset
    
    def get_serializer_class(self):
        """Default serializer"""
        return ScalingActionSerializer
    
    def create(self, request, *args, **kwargs):
        """Create a scaling action"""
        # Verify experiment exists
        experiment_id = request.data.get('experiment_id')
        if experiment_id:
            experiment = ExperimentService.check_experiment_exists(experiment_id)
            if experiment is None:
                return Response(
                    {'detail': 'Experiment not found'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Validate and create scaling action
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save(performed_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response({'detail': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def rollback_scaling_action(request, id):
    """
    POST /optimization/scaling/{id}/rollback/
    """
    # Verify scaling action exists
    try:
        scaling_action = ScalingAction.objects.get(id=id)
    except ScalingAction.DoesNotExist:
        return Response(
            {'detail': 'Scaling action not found'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Verify scaling action has not been rolled back
    if RollbackHistoryService.check_rollback_history_exists_by_scaling_action_id(id):
        return Response(
            {'detail': 'Scaling action has already been rolled back'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Validate rollback data
    serializer = ScalingActionRollbackSerializer(data=request.data)
    if serializer.is_valid():
        # Create rollback history record
        rollback_history = RollbackHistory.objects.create(
            scaling_action_id=scaling_action,
            reason=serializer.validated_data['reason'],
            performed_by=request.user
        )
        
        return Response(
            RollbackHistorySerializer(rollback_history).data,
            status=status.HTTP_201_CREATED
        )
    
    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


# ==================== SCALING PLAN VIEWS ====================

class ScalingPlanListCreateView(generics.ListCreateAPIView):
    """
    GET /optimization/scaling-plans/
    POST /optimization/scaling-plans/

    Scaling plans are always associated with a Task(type='scaling').
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ScalingPlanSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return ScalingPlan.objects.none()

        # User must be active member of the task's project
        accessible_project_ids = ProjectMember.objects.filter(
            user=user,
            is_active=True,
        ).values_list("project_id", flat=True)

        queryset = ScalingPlan.objects.select_related("task", "task__project").filter(
            task__project_id__in=accessible_project_ids
        )

        task_pk = resolve_pk_for(Task, self.request.query_params.get("task_id"))
        if task_pk:
            queryset = queryset.filter(task_id=task_pk)

        return queryset

    def perform_create(self, serializer):
        task = serializer.validated_data.get("task")
        if task is None:
            raise DRFValidationError({"task": "Task is required for scaling plan."})

        # Ensure task is of type 'scaling'
        if task.type != "scaling":
            raise DRFValidationError(
                {"task": 'Scaling plan can only be created for tasks of type "scaling".'}
            )

        # Ensure user has access to task's project
        user = self.request.user
        has_membership = ProjectMember.objects.filter(
            user=user,
            project=task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise PermissionDenied("You do not have access to this task.")

        # Enforce one-to-one relation: prevent duplicate plan per task
        if hasattr(task, "scaling_plan"):
            raise DRFValidationError(
                {"task": "Scaling plan already exists for this task."}
            )

        plan = serializer.save()
        task.link_to_object(plan)
        task.save()


class ScalingPlanRetrieveUpdateView(generics.RetrieveUpdateAPIView):
    """
    GET /optimization/scaling-plans/{id}/
    PATCH /optimization/scaling-plans/{id}/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ScalingPlanSerializer
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return ScalingPlan.objects.none()

        accessible_project_ids = ProjectMember.objects.filter(
            user=user,
            is_active=True,
        ).values_list("project_id", flat=True)

        return ScalingPlan.objects.select_related("task", "task__project").filter(
            task__project_id__in=accessible_project_ids
        )


class ScalingStepListCreateView(generics.ListCreateAPIView):
    """
    GET /optimization/scaling-plans/{plan_id}/steps/
    POST /optimization/scaling-plans/{plan_id}/steps/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ScalingStepSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return ScalingStep.objects.none()

        plan_id = self.kwargs.get("plan_id")
        base_qs = ScalingStep.objects.select_related(
            "plan", "plan__task", "plan__task__project"
        )
        queryset = base_qs.filter(plan_id=plan_id)

        accessible_project_ids = ProjectMember.objects.filter(
            user=user,
            is_active=True,
        ).values_list("project_id", flat=True)

        return queryset.filter(plan__task__project_id__in=accessible_project_ids)

    def perform_create(self, serializer):
        plan_id = self.kwargs.get("plan_id")
        plan = get_object_or_404(
            ScalingPlan.objects.select_related("task", "task__project"), id=plan_id
        )

        user = self.request.user
        has_membership = ProjectMember.objects.filter(
            user=user,
            project=plan.task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise PermissionDenied("You do not have access to this task.")

        serializer.save(plan=plan)


class ScalingStepRetrieveUpdateView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /optimization/scaling-steps/{id}/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ScalingStepSerializer
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return ScalingStep.objects.none()

        accessible_project_ids = ProjectMember.objects.filter(
            user=user,
            is_active=True,
        ).values_list("project_id", flat=True)

        base_qs = ScalingStep.objects.select_related(
            "plan", "plan__task", "plan__task__project"
        )

        return base_qs.filter(plan__task__project_id__in=accessible_project_ids)


# ==================== OPTIMIZATION VIEWS ====================

class OptimizationListCreateView(generics.ListCreateAPIView):
    """
    GET /optimization/optimizations/
    POST /optimization/optimizations/

    Optimizations are always associated with a Task(type='optimization').
    """

    permission_classes = [IsAuthenticated]
    serializer_class = OptimizationSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Optimization.objects.none()

        # User must be active member of the task's project
        accessible_project_ids = ProjectMember.objects.filter(
            user=user,
            is_active=True,
        ).values_list("project_id", flat=True)

        queryset = Optimization.objects.select_related("task", "task__project").filter(
            task__project_id__in=accessible_project_ids
        )

        # Optional filtering by query params
        task_pk = resolve_pk_for(Task, self.request.query_params.get("task_id"))
        if task_pk:
            queryset = queryset.filter(task_id=task_pk)

        execution_status = self.request.query_params.get("execution_status")
        if execution_status:
            queryset = queryset.filter(execution_status=execution_status)

        action_type = self.request.query_params.get("action_type")
        if action_type:
            queryset = queryset.filter(action_type=action_type)

        return queryset

    def perform_create(self, serializer):
        task = serializer.validated_data.get("task")
        if task is None:
            raise DRFValidationError({"task": "Task is required for optimization."})

        # Ensure task is of type 'optimization'
        if task.type != "optimization":
            raise DRFValidationError(
                {"task": 'Optimization can only be created for tasks of type "optimization".'}
            )

        # Ensure user has access to task's project
        user = self.request.user
        has_membership = ProjectMember.objects.filter(
            user=user,
            project=task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise PermissionDenied("You do not have access to this task.")

        # Enforce one-to-one relation: prevent duplicate optimization per task
        if hasattr(task, "optimization"):
            raise DRFValidationError(
                {"task": "Optimization already exists for this task."}
            )

        serializer.save()


class OptimizationRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET /optimization/optimizations/{id}/
    PATCH /optimization/optimizations/{id}/
    DELETE /optimization/optimizations/{id}/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = OptimizationSerializer
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Optimization.objects.none()

        accessible_project_ids = ProjectMember.objects.filter(
            user=user,
            is_active=True,
        ).values_list("project_id", flat=True)

        return Optimization.objects.select_related("task", "task__project").filter(
            task__project_id__in=accessible_project_ids
        )


# ==================== BUDGET PACING VIEWS ====================

def _get_accessible_campaign(user, slug):
    """Resolve a campaign by slug, scoped to the user's project memberships.

    Mirrors CampaignViewSet.get_queryset so pacing cannot leak a campaign the
    user would not otherwise be able to read. Returns None when not found or
    not accessible — callers answer 404 either way, so membership is not
    disclosed.
    """
    from campaign.models import Campaign

    accessible_project_ids = set(
        ProjectMember.objects.filter(
            user=user,
            is_active=True
        ).values_list('project_id', flat=True)
    )
    if not accessible_project_ids:
        return None

    return Campaign.objects.filter(
        slug=slug,
        project_id__in=accessible_project_ids,
        is_deleted=False
    ).first()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def campaign_pacing(request, slug):
    """
    GET /optimization/campaigns/{slug}/pacing/
    """
    campaign = _get_accessible_campaign(request.user, slug)
    if campaign is None:
        return Response(
            {'detail': 'Campaign not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Reverse OneToOne raises an AttributeError subclass when absent, so getattr
    # with a default is safe here.
    forecast = getattr(campaign, 'pacing_forecast', None)
    if forecast is None:
        # No nightly run has covered this campaign yet (newly created, or first
        # deploy) — compute once now so the UI is never blank.
        forecast = PacingService.recompute_for_campaign(campaign)

    return Response(CampaignPacingForecastSerializer(forecast).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def recompute_campaign_pacing(request, slug):
    """
    POST /optimization/campaigns/{slug}/pacing/recompute/
    """
    campaign = _get_accessible_campaign(request.user, slug)
    if campaign is None:
        return Response(
            {'detail': 'Campaign not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    forecast = PacingService.recompute_for_campaign(campaign)
    return Response(CampaignPacingForecastSerializer(forecast).data)
