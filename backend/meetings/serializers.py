import json
from typing import Iterable
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist

from django.shortcuts import get_object_or_404
from rest_framework import serializers

from core.models import Project, ProjectMember
from meetings.models import (
    Meeting,
    AgendaItem,
    ParticipantLink,
    ArtifactLink,
    MeetingDocument,
    MeetingActionItem,
    MeetingAuditLog,
)
from meetings.knowledge_links import (
    generated_decisions_payload,
    generated_tasks_payload,
    related_decisions_payload,
    related_tasks_payload,
)
from meetings.services import (
    ensure_meeting_type_definition,
    MEETING_LIST_ORDERING_MAP,
)
from zoom_integration.post_meeting_payload import build_zoom_post_meeting_payload


def meeting_participants_for_api(meeting: Meeting, request=None) -> list[dict]:
    result = []
    for link in meeting.participant_links.select_related("user").all():
        user = link.user
        avatar_url = None
        if user.avatar:
            avatar_url = (
                request.build_absolute_uri(user.avatar.url)
                if request
                else user.avatar.url
            )
        result.append({
            "user_id": link.user_id,
            "role": link.role,
            "username": user.username,
            "avatar": avatar_url,
        })
    return result


def meeting_tags_for_api(meeting: Meeting) -> list[dict]:
    return [
        {"slug": a.tag_definition.slug, "label": a.tag_definition.label}
        for a in meeting.tag_assignments.all()
    ]


class MeetingSerializer(serializers.ModelSerializer):
    """
    Optional write-only participant_user_ids on create — see MeetingViewSet.perform_create.

    ``meeting_type`` is the API-facing string; it maps to structured ``MeetingTypeDefinition``.
    """

    participant_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    meeting_type = serializers.CharField(write_only=True, max_length=160, required=False)
    meeting_type_slug = serializers.CharField(
        read_only=True, source="type_definition.slug"
    )
    type_definition_id = serializers.IntegerField(
        read_only=True, source="type_definition.id"
    )
    participants = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    generated_decisions = serializers.SerializerMethodField()
    generated_tasks = serializers.SerializerMethodField()
    generated_decisions_count = serializers.IntegerField(read_only=True, source="decision_count")
    generated_tasks_count = serializers.IntegerField(read_only=True, source="task_count")
    related_decisions = serializers.SerializerMethodField()
    related_tasks = serializers.SerializerMethodField()
    zoom_post_meeting = serializers.SerializerMethodField()

    class Meta:
        model = Meeting
        fields = ['slug', 
            "id",
            "project",
            "title",
            "meeting_type",
            "meeting_type_slug",
            "type_definition_id",
            "objective",
            "summary",
            "scheduled_date",
            "scheduled_time",
            "external_reference",
            "layout_config",
            "status",
            "is_archived",
            "minutes_published",
            "participants",
            "tags",
            "generated_decisions",
            "generated_tasks",
            "generated_decisions_count",
            "generated_tasks_count",
            "related_decisions",
            "related_tasks",
            "created_at",
            "updated_at",
            "participant_user_ids",
            "zoom_post_meeting",
        ]
        read_only_fields = ['slug', 
            "id",
            "project",
            "meeting_type_slug",
            "type_definition_id",
            "created_at",
            "updated_at",
            "generated_decisions",
            "generated_tasks",
            "generated_decisions_count",
            "generated_tasks_count",
            "related_decisions",
            "related_tasks",
            "zoom_post_meeting",
            "status",
            "is_archived",
        ]

    def get_participants(self, obj):
        return meeting_participants_for_api(obj, self.context.get("request"))

    def get_tags(self, obj):
        return meeting_tags_for_api(obj)

    def get_generated_decisions(self, obj):
        return generated_decisions_payload(obj)

    def get_generated_tasks(self, obj):
        return generated_tasks_payload(obj)

    def get_related_decisions(self, obj):
        return related_decisions_payload(obj)

    def get_related_tasks(self, obj):
        return related_tasks_payload(obj)

    def get_zoom_post_meeting(self, obj: Meeting):
        try:
            zd = obj.zoom_meeting_data
        except ObjectDoesNotExist:
            return None
        return build_zoom_post_meeting_payload(zd)

    def validate(self, attrs):
        request = self.context.get("request")
        if self.instance is None and request and request.method == "POST":
            if not attrs.get("meeting_type"):
                raise serializers.ValidationError(
                    {"meeting_type": ["This field is required."]}
                )
        return attrs

    def create(self, validated_data):
        validated_data.pop("participant_user_ids", None)
        label = validated_data.pop("meeting_type")
        project = validated_data["project"]
        validated_data["type_definition"] = ensure_meeting_type_definition(project, label)
        return Meeting.objects.create(**validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["meeting_type"] = instance.type_definition.label
        lc = data.get("layout_config")
        if lc is None:
            data["layout_config"] = []
        elif isinstance(lc, (list, dict)):
            pass
        else:
            data["layout_config"] = []
        return data

    def validate_layout_config(self, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            blocks = value.get("blocks")
            if not isinstance(blocks, list):
                raise serializers.ValidationError(
                    "layout_config.blocks must be a list when layout_config is an object."
                )
            nested = value.get("nestedSections")
            if nested is not None and not isinstance(nested, list):
                raise serializers.ValidationError(
                    "layout_config.nestedSections must be a list or omitted."
                )
            try:
                json.dumps(value)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError(
                    "layout_config must be JSON-serializable."
                ) from exc
            return value
        raise serializers.ValidationError(
            "layout_config must be a list of blocks or an object with a blocks list."
        )

    def update(self, instance, validated_data):
        # Participants are managed via the participants sub-resource
        validated_data.pop("participant_user_ids", None)
        if "meeting_type" in validated_data:
            label = validated_data.pop("meeting_type")
            validated_data["type_definition"] = ensure_meeting_type_definition(
                instance.project, label
            )
        return super().update(instance, validated_data)


class MeetingKnowledgeDiscoveryQuerySerializer(serializers.Serializer):
    """
    Validates query params for project-scoped meeting list / knowledge discovery (strict filters).
    """

    q = serializers.CharField(required=False, max_length=500, allow_blank=True)
    meeting_type = serializers.ListField(
        child=serializers.CharField(max_length=80),
        required=False,
        allow_empty=True,
    )
    tag = serializers.CharField(required=False, max_length=80, allow_blank=True)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    is_archived = serializers.BooleanField(required=False)
    has_generated_decisions = serializers.BooleanField(required=False)
    has_generated_tasks = serializers.BooleanField(required=False)
    ordering = serializers.CharField(required=False, allow_blank=True)
    page = serializers.IntegerField(required=False, min_value=1, default=1)

    def validate_ordering(self, value):
        if value in (None, ""):
            return None
        if value not in MEETING_LIST_ORDERING_MAP:
            raise serializers.ValidationError(
                f'Invalid ordering "{value}". Allowed: {", ".join(sorted(MEETING_LIST_ORDERING_MAP))}.'
            )
        return value

    def validate_meeting_type(self, value):
        """Repeated `meeting_type` query params → OR over type slugs (see ListField.get_value + QueryDict)."""
        if not value:
            return None
        return self._unique_slug_strings(value, "meeting_type", 80) or None

    @staticmethod
    def _unique_positive_ints(values: Iterable[str], field_name: str) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for raw in values:
            if raw is None or str(raw).strip() == "":
                continue
            try:
                n = int(raw)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError(
                    {field_name: ["Invalid integer."]}
                ) from exc
            if n < 1:
                raise serializers.ValidationError(
                    {field_name: ["Must be >= 1."]}
                )
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    @staticmethod
    def _unique_slug_strings(
        values: Iterable[str], field_name: str, max_len: int
    ) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in values:
            s = str(raw).strip()
            if not s:
                continue
            if len(s) > max_len:
                raise serializers.ValidationError(
                    {
                        field_name: [
                            f"Ensure this value has at most {max_len} characters (got {len(s)})."
                        ]
                    }
                )
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def validate(self, attrs):
        request = self.context.get("request")
        qp = getattr(request, "query_params", None) if request else None

        participant_ids: list[int] = []
        exclude_ids: list[int] = []
        if qp is not None:
            participant_ids = self._unique_positive_ints(
                qp.getlist("participant"), "participant"
            )
            exclude_ids = self._unique_positive_ints(
                qp.getlist("exclude_participant"), "exclude_participant"
            )

        attrs["participant"] = participant_ids if participant_ids else None
        attrs["exclude_participant"] = exclude_ids if exclude_ids else None

        if qp is not None:
            if "participant" not in qp:
                attrs.pop("participant", None)
            if "exclude_participant" not in qp:
                attrs.pop("exclude_participant", None)
            if "meeting_type" not in qp:
                attrs.pop("meeting_type", None)

        q = attrs.get("q")
        if q is not None:
            stripped = q.strip()
            attrs["q"] = stripped if stripped else None

        v = attrs.get("tag")
        if v is not None and not str(v).strip():
            attrs["tag"] = None
        elif v is not None:
            attrs["tag"] = str(v).strip()

        if attrs.get("date_from") and attrs.get("date_to"):
            if attrs["date_from"] > attrs["date_to"]:
                raise serializers.ValidationError(
                    {"date_to": ["Must be on or after date_from."]}
                )

        pl = attrs.get("participant") or []
        el = attrs.get("exclude_participant") or []
        if pl and el and set(pl) & set(el):
            raise serializers.ValidationError(
                {
                    "exclude_participant": [
                        "Cannot include and exclude the same user(s)."
                    ]
                }
            )

        # meeting_type / tag slugs: do not require a row in MeetingTypeDefinition /
        # MeetingTagDefinition. Unknown or never-used slugs yield an empty list (discovery UX).

        if hasattr(self, "initial_data") and self.initial_data is not None:
            if "is_archived" not in self.initial_data:
                attrs.pop("is_archived", None)
            if "has_generated_decisions" not in self.initial_data:
                attrs.pop("has_generated_decisions", None)
            if "has_generated_tasks" not in self.initial_data:
                attrs.pop("has_generated_tasks", None)

        return attrs


class MeetingListSerializer(serializers.ModelSerializer):
    """
    Lightweight row for meeting list / search (no objective, notes, etc.).
    """

    meeting_type = serializers.CharField(
        read_only=True, source="type_definition.label"
    )
    meeting_type_slug = serializers.CharField(
        read_only=True, source="type_definition.slug"
    )
    participants = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    decision_count = serializers.IntegerField(read_only=True)
    task_count = serializers.IntegerField(read_only=True)
    generated_decisions_count = serializers.IntegerField(read_only=True, source="decision_count")
    generated_tasks_count = serializers.IntegerField(read_only=True, source="task_count")
    generated_decisions = serializers.SerializerMethodField()
    generated_tasks = serializers.SerializerMethodField()
    search_snippet = serializers.SerializerMethodField()
    snippet_source = serializers.SerializerMethodField()
    related_decisions = serializers.SerializerMethodField()
    related_tasks = serializers.SerializerMethodField()

    class Meta:
        model = Meeting
        fields = [
            "id",
            "slug",
            "title",
            "summary",
            "scheduled_date",
            "status",
            "meeting_type",
            "meeting_type_slug",
            "participants",
            "tags",
            "decision_count",
            "task_count",
            "generated_decisions_count",
            "generated_tasks_count",
            "generated_decisions",
            "generated_tasks",
            "related_decisions",
            "related_tasks",
            "is_archived",
            "search_snippet",
            "snippet_source",
        ]

    def get_participants(self, obj):
        return meeting_participants_for_api(obj, self.context.get("request"))

    def get_tags(self, obj):
        return meeting_tags_for_api(obj)

    def get_generated_decisions(self, obj):
        return generated_decisions_payload(obj)

    def get_generated_tasks(self, obj):
        return generated_tasks_payload(obj)

    def get_related_decisions(self, obj):
        return related_decisions_payload(obj)

    def get_related_tasks(self, obj):
        return related_tasks_payload(obj)

    def get_search_snippet(self, obj):
        title_hl = getattr(obj, "title_headline", None) or ""
        if "<mark>" in title_hl:
            return ""
        summary_hl = getattr(obj, "summary_headline", None) or ""
        transcript_hl = getattr(obj, "transcript_headline", None) or ""
        if "<mark>" in summary_hl:
            return summary_hl
        if "<mark>" in transcript_hl:
            return transcript_hl
        return ""

    def get_snippet_source(self, obj):
        title_hl = getattr(obj, "title_headline", None) or ""
        summary_hl = getattr(obj, "summary_headline", None) or ""
        transcript_hl = getattr(obj, "transcript_headline", None) or ""
        if "<mark>" in title_hl:
            return "title"
        if "<mark>" in summary_hl:
            return "summary"
        if "<mark>" in transcript_hl:
            return "transcript"
        return None


class AgendaItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgendaItem
        fields = ["id", "meeting", "content", "order_index", "is_priority"]
        read_only_fields = ["id", "meeting"]


class ParticipantLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParticipantLink
        fields = ["id", "meeting", "user", "role"]
        read_only_fields = ["id", "meeting"]

    def validate(self, attrs):
        """
        Enforce unique (meeting, user) at the serializer level so the API
        returns a 400 ValidationError instead of a database IntegrityError.
        """

        meeting = self.context.get("meeting")
        user = attrs.get("user")

        if meeting and user:
            exists_qs = ParticipantLink.objects.filter(meeting=meeting, user=user)
            if self.instance is not None:
                exists_qs = exists_qs.exclude(pk=self.instance.pk)

            if exists_qs.exists():
                raise serializers.ValidationError(
                    {
                        "non_field_errors": [
                            "Participant with this user already exists for this meeting."
                        ]
                    }
                )

        return attrs


class ArtifactLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArtifactLink
        fields = ["id", "meeting", "artifact_type", "artifact_id"]
        read_only_fields = ["id", "meeting"]



class MeetingLifecycleSerializer(serializers.Serializer):
    """Read-only: current state and available next transitions."""

    status = serializers.CharField(read_only=True)
    available_transitions = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )


class TransitionRequestSerializer(serializers.Serializer):
    """Validates the body of a transition POST request."""

    to_state = serializers.ChoiceField(choices=[c[0] for c in Meeting.STATUS_CHOICES])
class MeetingDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = apps.get_model("meetings", "MeetingDocument")
        fields = [
            "id",
            "meeting",
            "content",
            "yjs_state",
            "last_edited_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "meeting", "last_edited_by", "created_at", "updated_at"]


class MeetingActionItemSerializer(serializers.ModelSerializer):
    """CRUD for meeting follow-up action items (before task conversion)."""

    converted_task_id = serializers.SerializerMethodField()

    class Meta:
        model = MeetingActionItem
        fields = [
            "id",
            "meeting",
            "title",
            "description",
            "order_index",
            "is_resolved",
            "created_at",
            "updated_at",
            "converted_task_id",
        ]
        read_only_fields = [
            "id",
            "meeting",
            "created_at",
            "updated_at",
            "converted_task_id",
        ]

    def get_converted_task_id(self, obj):
        try:
            return obj.derived_task.id
        except (ObjectDoesNotExist, AttributeError):
            return None


class ActionItemConvertSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    type = serializers.CharField(default="execution")
    current_approver_id = serializers.IntegerField(required=False, allow_null=True)
    create_as_draft = serializers.BooleanField(default=False)

    def validate_priority(self, value):
        if value == "":
            return None
        return value


class BulkActionItemConvertItemSerializer(serializers.Serializer):
    action_item_id = serializers.IntegerField()
    owner_id = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    type = serializers.CharField(default="execution")
    current_approver_id = serializers.IntegerField(required=False, allow_null=True)
    create_as_draft = serializers.BooleanField(default=False)

    def validate_priority(self, value):
        if value == "":
            return None
        return value


class BulkActionItemConvertSerializer(serializers.Serializer):
    items = serializers.ListField(child=BulkActionItemConvertItemSerializer(), min_length=1)


class AuditLogActorSerializer(serializers.Serializer):
    """Serializer for actor info in audit log responses."""
    id = serializers.IntegerField()
    display_name = serializers.SerializerMethodField()
    email = serializers.EmailField()

    def get_display_name(self, obj):
        full_name = f"{obj.first_name} {obj.last_name}".strip()
        return full_name if full_name else obj.username


class MeetingAuditLogSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for MeetingAuditLog entries.
    Nested actor info with null fallback for system events.
    """
    actor = AuditLogActorSerializer(read_only=True, allow_null=True)

    class Meta:
        model = MeetingAuditLog
        fields = ['id', 'event_type', 'timestamp', 'actor', 'before', 'after', 'context']
        read_only_fields = fields

