from django.urls import path
from . import views

app_name = 'optimization'

# API URL patterns
urlpatterns = [
    # ==================== EXPERIMENT ENDPOINTS ====================

    # List and create experiments
    path('experiments/', views.ExperimentListCreateView.as_view(), name='experiment-list-create'),

    # Update specific experiment
    path('experiments/<int:id>/', views.ExperimentUpdateView.as_view(), name='experiment-update'),

    # Get experiment metrics
    path('experiments/<int:id>/metrics/', views.ExperimentMetricsView.as_view(), name='experiment-metrics'),

    # Ingest experiment metrics (Celery-friendly)
    path('experiments/<int:id>/metrics/ingest/', views.ingest_experiment_metrics, name='ingest-experiment-metrics'),

    # ==================== SCALING ACTION ENDPOINTS ====================

    # List and create scaling actions
    path('scaling/', views.ScalingActionListCreateView.as_view(), name='scaling-list-create'),

    # Rollback specific scaling action
    path('scaling/<int:id>/rollback/', views.rollback_scaling_action, name='rollback-scaling-action'),

    # ==================== SCALING PLAN & STEP ENDPOINTS ====================

    # List and create scaling plans
    path('scaling-plans/', views.ScalingPlanListCreateView.as_view(), name='scaling-plan-list-create'),

    # Retrieve/update a specific scaling plan
    path('scaling-plans/<int:id>/', views.ScalingPlanRetrieveUpdateView.as_view(), name='scaling-plan-detail'),

    # List and create steps for a specific plan
    path(
        'scaling-plans/<int:plan_id>/steps/',
        views.ScalingStepListCreateView.as_view(),
        name='scaling-step-list-create',
    ),

    # Retrieve/update/delete a specific scaling step
    path(
        'scaling-steps/<int:id>/',
        views.ScalingStepRetrieveUpdateView.as_view(),
        name='scaling-step-detail',
    ),

    # ==================== OPTIMIZATION ENDPOINTS ====================

    # List and create optimizations
    path('optimizations/', views.OptimizationListCreateView.as_view(), name='optimization-list-create'),

    # Retrieve/update/delete a specific optimization
    path('optimizations/<int:id>/', views.OptimizationRetrieveUpdateDestroyView.as_view(), name='optimization-detail'),

    # ==================== BUDGET PACING ENDPOINTS ====================

    # Latest pacing forecast for a campaign
    path('campaigns/<slug:slug>/pacing/', views.campaign_pacing, name='campaign-pacing'),

    # Recompute a campaign's pacing forecast on demand
    path(
        'campaigns/<slug:slug>/pacing/recompute/',
        views.recompute_campaign_pacing,
        name='campaign-pacing-recompute',
    ),
]
