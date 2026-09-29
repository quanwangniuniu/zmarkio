from rest_framework import serializers
from django.db import transaction

from core.models import Project, ProjectMember
from core.slug_mixins import resolve_pk_for
from report.kpi_registry import KPIFormulaError, validate_formula
from report.models import CustomKPI, ReportTask, ReportTaskKeyAction
from task.models import Task


class ReportTaskKeyActionSerializer(serializers.ModelSerializer):
    report_task = serializers.IntegerField(source="report_task_id", read_only=True, required=False)

    class Meta:
        model = ReportTaskKeyAction
        fields = ["id", "report_task", "order_index", "action_text", "created_at", "updated_at"]
        read_only_fields = ["id", "report_task", "created_at", "updated_at"]


class ReportTaskSerializer(serializers.ModelSerializer):
    audience_type = serializers.ChoiceField(
        required=False,
        choices=ReportTask.AudienceType.choices,
        default=ReportTask.AudienceType.SELF,
    )
    audience_details = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    context = serializers.JSONField(required=False, allow_null=True, default=dict)
    outcome_summary = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    narrative_explanation = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    is_complete = serializers.BooleanField(read_only=True)
    prompt_template = serializers.SerializerMethodField(read_only=True)

    # Expose key actions as ordered objects.
    key_actions = ReportTaskKeyActionSerializer(many=True, read_only=True)

    class Meta:
        model = ReportTask
        fields = [
            "id",
            "task",
            "audience_type",
            "audience_details",
            "audience_prompt_version",
            "prompt_template",
            "context",
            "outcome_summary",
            "narrative_explanation",
            "key_actions",
            "is_complete",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "audience_prompt_version",
            "prompt_template",
            "is_complete",
            "created_at",
            "updated_at",
        ]

    def validate_context(self, value):
        """Validate context structure."""
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("Context must be a JSON object.")
        
        # Ensure required structure
        if "situation" not in value:
            value["situation"] = ""
        if "what_changed" not in value:
            value["what_changed"] = ""
        
        # Validate reporting_period if present
        if "reporting_period" in value and value["reporting_period"] is not None:
            rp = value["reporting_period"]
            if not isinstance(rp, dict):
                raise serializers.ValidationError("reporting_period must be an object or null.")
            
            rp_type = rp.get("type")
            if rp_type not in [None, "last_week", "this_month", "custom"]:
                raise serializers.ValidationError(
                    "reporting_period.type must be one of: last_week, this_month, custom, or null."
                )
            
            # Validate dates based on type
            if rp_type == "custom":
                # Custom type requires dates
                if not rp.get("start_date") or not rp.get("end_date"):
                    raise serializers.ValidationError(
                        "reporting_period.start_date and end_date are required when type is 'custom'."
                    )
            # last_week and this_month can have dates (optional, calculated on frontend)
        
        return value

    def get_prompt_template(self, obj: ReportTask) -> dict:
        return obj.resolved_prompt_template


class ReportCreateSerializer(serializers.ModelSerializer):
    """Create report (no key_actions); key actions are created via nested key-actions endpoints."""

    task = serializers.PrimaryKeyRelatedField(queryset=Task.objects.all())
    audience_type = serializers.ChoiceField(
        required=False,
        choices=ReportTask.AudienceType.choices,
        default=ReportTask.AudienceType.SELF,
    )
    audience_details = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    context = serializers.JSONField(required=False, allow_null=True, default=dict)
    outcome_summary = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    narrative_explanation = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")

    class Meta:
        model = ReportTask
        fields = [
            "task",
            "audience_type",
            "audience_details",
            "context",
            "outcome_summary",
            "narrative_explanation",
        ]

    def validate_task(self, task: Task) -> Task:
        if task.type != "report":
            raise serializers.ValidationError(
                'ReportTask details can only be created for tasks of type "report".'
            )
        return task

    def validate_context(self, value):
        """Validate context structure."""
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("Context must be a JSON object.")

        # Ensure required structure
        if "situation" not in value:
            value["situation"] = ""
        if "what_changed" not in value:
            value["what_changed"] = ""

        # Validate reporting_period if present
        if "reporting_period" in value and value["reporting_period"] is not None:
            rp = value["reporting_period"]
            if not isinstance(rp, dict):
                raise serializers.ValidationError("reporting_period must be an object or null.")

            rp_type = rp.get("type")
            if rp_type not in [None, "last_week", "this_month", "custom"]:
                raise serializers.ValidationError(
                    "reporting_period.type must be one of: last_week, this_month, custom, or null."
                )

            # Validate dates based on type
            if rp_type == "custom":
                # Custom type requires dates
                if not rp.get("start_date") or not rp.get("end_date"):
                    raise serializers.ValidationError(
                        "reporting_period.start_date and end_date are required when type is 'custom'."
                    )
            # last_week and this_month can have dates (optional, calculated on frontend)

        return value

    def validate(self, attrs):
        audience_type = attrs.get("audience_type")
        audience_details = (attrs.get("audience_details") or "").strip()
        if audience_type == ReportTask.AudienceType.OTHER and not audience_details:
            raise serializers.ValidationError(
                {"audience_details": "Audience details are required when audience type is 'other'."}
            )

        return attrs

    def _ensure_user_can_access_task(self, task: Task):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise serializers.ValidationError({"task": "Authentication required."})
        has_membership = ProjectMember.objects.filter(
            user=user,
            project=task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise serializers.ValidationError({"task": "You do not have access to this task."})

    @transaction.atomic
    def create(self, validated_data):
        task = validated_data.get("task")
        self._ensure_user_can_access_task(task)
        if hasattr(task, "report_task"):
            raise serializers.ValidationError({"task": "ReportTask details already exist for this task."})
        report_task = super().create(validated_data)
        task.link_to_object(report_task)  # Link within transaction
        return report_task


class ReportUpdateSerializer(serializers.ModelSerializer):
    """Partial update; no task, no key_actions."""
    
    audience_type = serializers.ChoiceField(required=False, choices=ReportTask.AudienceType.choices)
    audience_details = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    context = serializers.JSONField(required=False, allow_null=True, default=dict)
    outcome_summary = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    narrative_explanation = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")

    class Meta:
        model = ReportTask
        fields = [
            "audience_type",
            "audience_details",
            "context",
            "outcome_summary",
            "narrative_explanation",
        ]

    def validate_context(self, value):
        """Validate context structure."""
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("Context must be a JSON object.")
        
        # Ensure required structure
        if "situation" not in value:
            value["situation"] = ""
        if "what_changed" not in value:
            value["what_changed"] = ""
        
        # Validate reporting_period if present
        if "reporting_period" in value and value["reporting_period"] is not None:
            rp = value["reporting_period"]
            if not isinstance(rp, dict):
                raise serializers.ValidationError("reporting_period must be an object or null.")
            
            rp_type = rp.get("type")
            if rp_type not in [None, "last_week", "this_month", "custom"]:
                raise serializers.ValidationError(
                    "reporting_period.type must be one of: last_week, this_month, custom, or null."
                )
            
            # Validate dates based on type
            if rp_type == "custom":
                # Custom type requires dates
                if not rp.get("start_date") or not rp.get("end_date"):
                    raise serializers.ValidationError(
                        "reporting_period.start_date and end_date are required when type is 'custom'."
                    )
            # last_week and this_month can have dates (optional, calculated on frontend)
        
        return value

    def validate(self, attrs):
        audience_type = attrs.get("audience_type", getattr(self.instance, "audience_type", None))
        if audience_type == ReportTask.AudienceType.OTHER:
            audience_details = (attrs.get("audience_details") or "").strip()
            existing = (getattr(self.instance, "audience_details", None) or "").strip() if self.instance else ""
            if not audience_details and not existing:
                raise serializers.ValidationError(
                    {"audience_details": "Audience details are required when audience type is 'other'."}
                )

        return attrs


class ReportKeyActionCreateSerializer(serializers.ModelSerializer):
    """Create key action; order_index 1-6, unique per report; max 6 per report."""

    class Meta:
        model = ReportTaskKeyAction
        fields = ["order_index", "action_text"]

    def validate_order_index(self, value):
        if value is None or not (1 <= value <= 6):
            raise serializers.ValidationError("order_index must be between 1 and 6.")
        return value

    def validate_action_text(self, value):
        if not value or not (value and value.strip()):
            raise serializers.ValidationError("action_text cannot be blank.")
        if len(value) > 280:
            raise serializers.ValidationError("action_text must be at most 280 characters.")
        return value.strip()

    def validate(self, attrs):
        report_task = self.context.get("report_task")
        if not report_task:
            return attrs
        order_index = attrs.get("order_index")
        if ReportTaskKeyAction.objects.filter(report_task=report_task, order_index=order_index).exists():
            raise serializers.ValidationError(
                {"order_index": "A key action with this order_index already exists for this report."}
            )
        if ReportTaskKeyAction.objects.filter(report_task=report_task).count() >= 6:
            raise serializers.ValidationError(
                "This report already has 6 key actions. Delete one before adding another."
            )
        return attrs


class ReportKeyActionUpdateSerializer(serializers.ModelSerializer):
    """Partial update for key action."""

    class Meta:
        model = ReportTaskKeyAction
        fields = ["order_index", "action_text"]

    def validate_order_index(self, value):
        if value is not None and not (1 <= value <= 6):
            raise serializers.ValidationError("order_index must be between 1 and 6.")
        return value

    def validate_action_text(self, value):
        if value is not None:
            if not value.strip():
                raise serializers.ValidationError("action_text cannot be blank.")
            if len(value) > 280:
                raise serializers.ValidationError("action_text must be at most 280 characters.")
            return value.strip()
        return value

    def validate(self, attrs):
        report_task = self.context.get("report_task")
        if not report_task or "order_index" not in attrs:
            return attrs
        order_index = attrs["order_index"]
        qs = ReportTaskKeyAction.objects.filter(report_task=report_task, order_index=order_index)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"order_index": "A key action with this order_index already exists for this report."}
            )
        return attrs


def _resolve_member_project(user, value) -> Project:
    """Resolve a slug-or-pk project value the user is an active member of."""
    project_pk = resolve_pk_for(Project, value)
    if not project_pk:
        raise serializers.ValidationError("No matching project.")
    is_member = ProjectMember.objects.filter(
        user=user,
        project_id=project_pk,
        is_active=True,
    ).exists()
    if not is_member:
        raise serializers.ValidationError("You do not have access to this project.")
    return Project.objects.get(pk=project_pk)


def _validate_kpi_formula(value: str) -> str:
    """Surface `KPIFormulaError` as a field error so the builder can inline it."""
    try:
        validate_formula(value)
    except KPIFormulaError as exc:
        raise serializers.ValidationError(exc.message) from exc
    return value.strip()


class CustomKPISerializer(serializers.ModelSerializer):
    """Read serializer. Values are computed only when the view supplies
    `metric_snapshot` in context, so a list costs one warehouse query, not one
    per KPI."""

    project = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    project_id = serializers.IntegerField(read_only=True)
    value = serializers.SerializerMethodField(read_only=True)
    error = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CustomKPI
        fields = [
            "id",
            "project",
            "project_id",
            "name",
            "description",
            "formula",
            "display_format",
            "value",
            "error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _evaluation(self, obj: CustomKPI):
        snapshot = self.context.get("metric_snapshot")
        if snapshot is None:
            return None
        cache = self.context.setdefault("_evaluation_cache", {})
        if obj.pk not in cache:
            from report.kpi_registry import evaluate_snapshot

            cache[obj.pk] = evaluate_snapshot(obj.formula, snapshot)
        return cache[obj.pk]

    def get_value(self, obj: CustomKPI):
        evaluation = self._evaluation(obj)
        if evaluation is None or not evaluation.ok:
            return None
        return str(evaluation.value)

    def get_error(self, obj: CustomKPI):
        evaluation = self._evaluation(obj)
        if evaluation is None or evaluation.ok:
            return None
        return {"code": evaluation.error_code, "message": evaluation.error_message}


class CustomKPICreateSerializer(serializers.ModelSerializer):
    project = serializers.CharField(write_only=True, help_text="Project slug or id")

    class Meta:
        model = CustomKPI
        fields = ["project", "name", "description", "formula", "display_format"]

    def validate_project(self, value):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise serializers.ValidationError("Authentication required.")
        return _resolve_member_project(user, value)

    def validate_name(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Name cannot be blank.")
        return value.strip()

    def validate_formula(self, value):
        return _validate_kpi_formula(value)

    def validate(self, attrs):
        project = attrs.get("project")
        name = attrs.get("name")
        if project and name and CustomKPI.objects.filter(project=project, name=name).exists():
            raise serializers.ValidationError(
                {"name": "A KPI with this name already exists in this project."}
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        validated_data["created_by"] = getattr(request, "user", None)
        return super().create(validated_data)


class CustomKPIUpdateSerializer(serializers.ModelSerializer):
    """Partial update. The project a KPI belongs to is fixed at creation."""

    class Meta:
        model = CustomKPI
        fields = ["name", "description", "formula", "display_format"]

    def validate_name(self, value):
        if value is not None and not value.strip():
            raise serializers.ValidationError("Name cannot be blank.")
        return value.strip() if value else value

    def validate_formula(self, value):
        return _validate_kpi_formula(value)

    def validate(self, attrs):
        name = attrs.get("name")
        if name and self.instance:
            clash = CustomKPI.objects.filter(
                project_id=self.instance.project_id,
                name=name,
            ).exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"name": "A KPI with this name already exists in this project."}
                )
        return attrs


class CustomKPIPreviewSerializer(serializers.Serializer):
    """Input for the unsaved-formula preview the builder calls while typing."""

    project = serializers.CharField()
    formula = serializers.CharField()
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate_project(self, value):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise serializers.ValidationError("Authentication required.")
        return _resolve_member_project(user, value)

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"start_date": "start_date must be on or before end_date."}
            )
        return attrs


class ReportTaskCreateUpdateSerializer(ReportTaskSerializer):
    """Create/update serializer that accepts `key_actions` as a list of strings."""

    task = serializers.PrimaryKeyRelatedField(queryset=Task.objects.all())
    key_actions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        help_text="List of key action strings in order (max 6)",
        write_only=True,
    )

    class Meta(ReportTaskSerializer.Meta):
        fields = ReportTaskSerializer.Meta.fields

    def validate_task(self, task: Task) -> Task:
        if task.type != "report":
            raise serializers.ValidationError(
                'ReportTask details can only be created for tasks of type "report".'
            )
        return task

    def validate_key_actions(self, value: list[str]) -> list[str]:
        if len(value) > 6:
            raise serializers.ValidationError("At most 6 key actions are allowed.")
        cleaned = [v.strip() for v in value]
        if any(not v for v in cleaned):
            raise serializers.ValidationError("Key actions cannot be blank.")
        return cleaned

    def validate(self, attrs):
        if self.instance and "task" in attrs and attrs["task"].id != self.instance.task_id:
            raise serializers.ValidationError({
                "task": "Task cannot be modified after report details are created."
            })

        audience_type = attrs.get("audience_type")
        audience_details = (attrs.get("audience_details") or "").strip()
        if audience_type == ReportTask.AudienceType.OTHER and not audience_details:
            raise serializers.ValidationError(
                {"audience_details": "Audience details are required when audience type is 'other'."}
            )
        return attrs

    def _ensure_user_can_access_task(self, task: Task):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise serializers.ValidationError({"task": "Authentication required."})

        has_membership = ProjectMember.objects.filter(
            user=user,
            project=task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise serializers.ValidationError({"task": "You do not have access to this task."})

    @transaction.atomic
    def create(self, validated_data):
        key_actions = validated_data.pop("key_actions", [])
        task = validated_data.get("task")
        self._ensure_user_can_access_task(task)

        if hasattr(task, "report_task"):
            raise serializers.ValidationError({"task": "ReportTask details already exist for this task."})

        report_task = super().create(validated_data)

        # Create key actions
        for idx, action_text in enumerate(key_actions, start=1):
            ReportTaskKeyAction.objects.create(
                report_task=report_task,
                order_index=idx,
                action_text=action_text,
            )

        task.link_to_object(report_task)  # Link within transaction
        return report_task

    def update(self, instance: ReportTask, validated_data):
        key_actions = validated_data.pop("key_actions", None)

        report_task = super().update(instance, validated_data)

        # If key_actions provided, replace the set (soft validation: allow empty).
        if key_actions is not None:
            ReportTaskKeyAction.objects.filter(report_task=report_task).delete()
            for idx, action_text in enumerate(key_actions, start=1):
                ReportTaskKeyAction.objects.create(
                    report_task=report_task,
                    order_index=idx,
                    action_text=action_text,
                )

        return report_task
