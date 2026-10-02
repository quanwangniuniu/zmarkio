from rest_framework import serializers
from .models import (
    CampaignPacingForecast,
    OptimizationExperiment,
    ExperimentMetric,
    ScalingAction,
    RollbackHistory,
    ScalingPlan,
    ScalingStep,
    Optimization,
)

def validate_campaign_id(campaign_id):
    """Validate campaign ID format"""
    if (not isinstance(campaign_id, str) or
        ':' not in campaign_id or
        len(campaign_id.split(':', 1)) != 2 or
        not campaign_id.split(':', 1)[0] or
        not campaign_id.split(':', 1)[1] or
        not campaign_id.split(':', 1)[1].isdigit()):
        return False
    return True


class OptimizationExperimentSerializer(serializers.ModelSerializer):
    """Unified serializer for OptimizationExperiment model"""
    
    class Meta:
        model = OptimizationExperiment
        fields = [
            'id', 'name', 'description', 'experiment_type', 'linked_campaign_ids',
            'hypothesis', 'start_date', 'end_date', 'status', 'created_by'
        ]
        read_only_fields = ['id', 'created_by']
    
    def validate(self, data):
        """Validate dates and campaign IDs"""
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        # For updates, get existing values if not provided
        if self.instance:
            if start_date is None:
                start_date = self.instance.start_date
            if end_date is None:
                end_date = self.instance.end_date
        
        # Validate date range
        if start_date and end_date and start_date >= end_date:
            raise serializers.ValidationError({
                'end_date': 'End date must be after start date.'
            })
        
        # Validate campaign IDs
        if 'linked_campaign_ids' in data:
            campaign_ids = data['linked_campaign_ids']
            
            # Validate it's a list
            if not isinstance(campaign_ids, list):
                raise serializers.ValidationError({
                    'linked_campaign_ids': 'Campaign IDs must be a list'
                })
            
            # Validate not empty
            if len(campaign_ids) == 0:
                raise serializers.ValidationError({
                    'linked_campaign_ids': 'At least one campaign ID is required'
                })
            
            # Validate each campaign ID format
            for campaign_id in campaign_ids:
                if (not validate_campaign_id(campaign_id)):
                    raise serializers.ValidationError({
                        'linked_campaign_ids': f"Campaign id must be a string in format 'platform:id' where platform is non-empty and id is numeric, got: {campaign_id}"
                    })
        
        return data


class ExperimentMetricSerializer(serializers.ModelSerializer):
    """Serializer for ExperimentMetric model"""
        
    class Meta:
        model = ExperimentMetric
        fields = [
            'id', 'experiment_id', 'metric_name',
            'metric_value', 'recorded_at'
        ]
        read_only_fields = ['id', 'recorded_at']


class MetricIngestSerializer(serializers.Serializer):
    """Serializer for metric ingestion (Celery-friendly)"""
    metric_name = serializers.CharField(required=True, allow_blank=False)
    metric_value = serializers.FloatField(required=True)
    
    class Meta:
        model = ExperimentMetric
        fields = ['metric_name','metric_value']


class ScalingActionSerializer(serializers.ModelSerializer):
    """Serializer for ScalingAction model"""

    def validate(self, data):
        """Validate campaign ID"""
        campaign_id = data.get('campaign_id')
        if campaign_id and not validate_campaign_id(campaign_id):
            raise serializers.ValidationError({f'campaign_id': f'Campaign id must be a string in format "platform:id" where platform is non-empty and id is numeric, got: {campaign_id}'})
        return data
        
    class Meta:
        model = ScalingAction
        fields = [
            'id', 'experiment_id', 'action_type',
            'action_details', 'campaign_id',
            'performed_by', 'performed_at'
        ]
        read_only_fields = ['id', 'performed_at', 'performed_by']


class ScalingActionRollbackSerializer(serializers.Serializer):
    """Serializer for rollback action"""
    reason = serializers.CharField(required=True, allow_blank=False)


class RollbackHistorySerializer(serializers.ModelSerializer):
    """Serializer for RollbackHistory model"""
    
    class Meta:
        model = RollbackHistory
        fields = [
            'id', 'scaling_action_id', 
            'reason', 'performed_by', 'performed_at'
        ]
        read_only_fields = ['id', 'performed_at', 'performed_by']


class ScalingStepSerializer(serializers.ModelSerializer):
    """Serializer for ScalingStep model"""

    class Meta:
        model = ScalingStep
        fields = [
            "id",
            "plan",
            "step_order",
            "name",
            "planned_change",
            "expected_metrics",
            "actual_metrics",
            "status",
            "scheduled_at",
            "executed_at",
            "notes",
            "stop_triggered",
            "related_scaling_action",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "plan"]


class ScalingPlanSerializer(serializers.ModelSerializer):
    """Serializer for ScalingPlan model"""

    steps = ScalingStepSerializer(many=True, read_only=True)

    class Meta:
        model = ScalingPlan
        fields = [
            "id",
            "task",
            "strategy",
            "scaling_target",
            "risk_considerations",
            "max_scaling_limit",
            "stop_conditions",
            "affected_entities",
            "expected_outcomes",
            "status",
            "started_at",
            "completed_at",
            "review_summary",
            "review_lessons_learned",
            "review_future_actions",
            "review_completed_at",
            "created_at",
            "updated_at",
            "steps",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class OptimizationSerializer(serializers.ModelSerializer):
    """Serializer for Optimization model"""

    class Meta:
        model = Optimization
        fields = [
            "id",
            "task",
            "affected_entity_ids",
            "triggered_metrics",
            "baseline_metrics",
            "observed_metrics",
            "action_type",
            "planned_action",
            "execution_status",
            "executed_at",
            "monitored_at",
            "outcome_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_task(self, task):
        """Validate that task type is 'optimization'"""
        if task.type != "optimization":
            raise serializers.ValidationError(
                'Task type must be "optimization" to link to an optimization.'
            )
        return task

    def validate_affected_entity_ids(self, value):
        """Validate affected_entity_ids structure and ID formats"""
        if value is None:
            return value

        if not isinstance(value, dict):
            raise serializers.ValidationError("affected_entity_ids must be a dictionary.")

        for key in ("campaign_ids", "ad_set_ids"):
            ids = value.get(key)
            if ids is None:
                continue
            if not isinstance(ids, list):
                raise serializers.ValidationError(f"{key} must be a list.")
            for item_id in ids:
                if not validate_campaign_id(item_id):
                    raise serializers.ValidationError(
                        f"Invalid id format in {key}: {item_id}. "
                        "Must be 'platform:id' where platform is non-empty and id is numeric."
                    )
        return value

    def validate(self, data):
        """Validate one-to-one relationship and task type"""
        task = data.get("task")
        if task and not self.instance:
            # Check if optimization already exists for this task
            if hasattr(task, "optimization"):
                raise serializers.ValidationError(
                    {"task": "Optimization already exists for this task."}
                )
        return data


# ==================== BUDGET PACING SERIALIZERS ====================

class CampaignPacingForecastSerializer(serializers.ModelSerializer):
    """Read-only view of a campaign's latest pacing forecast."""

    campaign_slug = serializers.CharField(source='campaign.slug', read_only=True)

    class Meta:
        model = CampaignPacingForecast
        fields = [
            'id', 'campaign', 'campaign_slug',
            'status', 'reason',
            'budget', 'spend_to_date', 'expected_spend_to_date',
            'projected_total_spend', 'suggested_daily_cap', 'avg_daily_spend',
            'pace_ratio',
            'total_days', 'days_elapsed', 'days_remaining',
            'seasonality_applied', 'dow_factors',
            'computed_for_date', 'computed_at',
        ]
        read_only_fields = fields
