import json

from django.db.models import Max
from django.utils import timezone
from decision.models import Decision
from decision.serializers import DecisionCommittedSerializer, DecisionListSerializer
import logging
from datetime import datetime
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from core.slug_mixins import SlugLookupViewSetMixin, resolve_lookup_kwargs, resolve_project_pk
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.filters import OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from core.models import Project, ProjectMember
from meetings.lifecycle import execute_transition, get_available_transitions
from meetings.models import (
    Meeting,
    MeetingDecisionOrigin,
    AgendaItem,
    ParticipantLink,
    ArtifactLink,
    MeetingTemplate,
    MeetingDocument,
    MeetingActionItem,
    MeetingAuditLog,
)
from meetings.serializers import (
    MeetingSerializer,
    MeetingListSerializer,
    MeetingKnowledgeDiscoveryQuerySerializer,
    AgendaItemSerializer,
    ParticipantLinkSerializer,
    ArtifactLinkSerializer,
    MeetingLifecycleSerializer,
    TransitionRequestSerializer,
    MeetingDocumentSerializer,
    MeetingActionItemSerializer,
    ActionItemConvertSerializer,
    BulkActionItemConvertSerializer,
    MeetingAuditLogSerializer,
)
from meetings.services import (
    reorder_agenda_items,
    get_or_create_meeting_document,
    normalize_text,
    notify_agenda_event,
    update_meeting_document_content,
    upsert_agenda_item_notification,
    user_has_meeting_document_access,
    meetings_base_queryset_for_project,
    apply_meeting_knowledge_filters,
    meeting_list_order_by_fields,
    hub_split_meeting_pks_for_project,
    update_meeting_title,
    update_meeting_objective,
    update_meeting_summary,
    add_participant,
    remove_participant,
    create_agenda_item,
    update_agenda_item,
    delete_agenda_item,
    create_action_item,
    update_action_item,
    update_meeting_type,
    update_meeting_datetime,
    update_meeting_tags,
    ensure_meeting_type_definition,
)
from notifications.models import NotificationCategory, NotificationEventType
from notifications.services import create_notification
from notifications.action_urls import meeting_action_url


logger = logging.getLogger(__name__)

# Default workspace module order (matches frontend initial blocks).
DEFAULT_MEETING_LAYOUT = [
    {"id": "header", "type": "header"},
    {"id": "agenda", "type": "agenda"},
    {"id": "participants", "type": "participants"},
    {"id": "artifacts", "type": "artifacts"},
]

def _ensure_project_membership(user, project: Project) -> None:
    if not ProjectMember.objects.filter(
        user=user,
        project=project,
        is_active=True,
    ).exists():
        raise PermissionDenied("You do not have access to this project.")


def _ensure_meeting_document_access(user, meeting: Meeting) -> None:
    if user_has_meeting_document_access(user.id, meeting):
        return
    raise PermissionDenied("You do not have access to this meeting document.")


def _assert_meeting_not_archived(meeting: Meeting) -> None:
    if meeting.status == Meeting.STATUS_ARCHIVED:
        raise PermissionDenied("Archived meetings are read-only.")


class ArchivedMeetingGuardMixin:
    """Blocks all write operations (create/update/partial_update/destroy) if the meeting is archived."""

    def _check_not_archived(self):
        _assert_meeting_not_archived(self.get_meeting())

    def create(self, request, *args, **kwargs):
        self._check_not_archived()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._check_not_archived()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._check_not_archived()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._check_not_archived()
        return super().destroy(request, *args, **kwargs)


class AuditLogPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200
    page_size_query_description = "Number of audit log entries per page (max 200)"

    def paginate_queryset(self, queryset, request, view=None):
        # Return empty page instead of 404 when page is out of range.
        try:
            return super().paginate_queryset(queryset, request, view)
        except Exception:
            self.page = None
            return []

    def get_paginated_response(self, data):
        if self.page is None:
            return Response({'count': 0, 'next': None, 'previous': None, 'results': []})
        return super().get_paginated_response(data)


class MeetingViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    serializer_class = MeetingSerializer
    permission_classes = [IsAuthenticated]

    def get_project(self) -> Project:
        project_id = self.kwargs.get("project_id")
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))
        _ensure_project_membership(self.request.user, project)
        return project

    def get_queryset(self):
        return meetings_base_queryset_for_project(self.get_project())

    def get_serializer_class(self):
        if self.action == "list":
            return MeetingListSerializer
        return MeetingSerializer

    def list(self, request, *args, **kwargs):
        project = self.get_project()
        query_serializer = MeetingKnowledgeDiscoveryQuerySerializer(
            data=request.query_params,
            context={"project": project, "request": request},
        )
        query_serializer.is_valid(raise_exception=True)
        filters = dict(query_serializer.validated_data)

        qs_base = meetings_base_queryset_for_project(project)

        incoming_pks, completed_pks = hub_split_meeting_pks_for_project(project)
        qs_incoming_lane = qs_base.filter(pk__in=incoming_pks).distinct()
        qs_completed_lane = qs_base.filter(pk__in=completed_pks).distinct()

        incoming_result_count = (
            apply_meeting_knowledge_filters(qs_incoming_lane, filters).distinct().count()
        )
        completed_result_count = (
            apply_meeting_knowledge_filters(qs_completed_lane, filters).distinct().count()
        )

        qs_filtered = apply_meeting_knowledge_filters(qs_base, filters).distinct()

        q = filters.get("q", "").strip()
        if q:
            from django.contrib.postgres.search import SearchHeadline, SearchQuery
            sq = SearchQuery(q)
            qs_filtered = qs_filtered.annotate(
                transcript_headline=SearchHeadline(
                    "transcript", sq,
                    start_sel="<mark>",
                    stop_sel="</mark>",
                    max_words=15,
                    min_words=5,
                    max_fragments=1,
                ),
                summary_headline=SearchHeadline(
                    "summary", sq,
                    start_sel="<mark>",
                    stop_sel="</mark>",
                    max_words=15,
                    min_words=5,
                    max_fragments=1,
                ),
                title_headline=SearchHeadline(
                    "title", sq,
                    start_sel="<mark>",
                    stop_sel="</mark>",
                    max_words=15,
                    min_words=5,
                    max_fragments=1,
                ),
            )

        ordering = filters.get("ordering") or "-created_at"
        qs = qs_filtered.order_by(*meeting_list_order_by_fields(ordering))

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            paginated = self.get_paginated_response(serializer.data)
            # Build a plain dict so hub fields always appear in JSON (avoid mutating ReturnDict edge cases).
            hub = {
                "incoming_lane_total": len(incoming_pks),
                "incoming_result_count": incoming_result_count,
                "completed_lane_total": len(completed_pks),
                "completed_result_count": completed_result_count,
                # Deprecated aliases (same values as *_result_count); kept for older clients.
                "incoming_lane_filtered": incoming_result_count,
                "completed_lane_filtered": completed_result_count,
            }
            payload = {**dict(paginated.data), **hub}
            return Response(payload)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get", "post"], url_path="decisions")
    def decisions(self, request, project_id=None, pk=None):
        project = self.get_project()
        meeting = self.get_object()

        if request.method == "GET":
            origins = (
                MeetingDecisionOrigin.objects
                .filter(meeting=meeting, decision__is_deleted=False)
                .select_related("decision", "created_by")
                .order_by("-origin_timestamp", "-created_at")
            )
            decisions = [origin.decision for origin in origins]
            serializer = DecisionListSerializer(
                decisions,
                many=True,
                context=self.get_serializer_context(),
            )
            return Response({"items": serializer.data})

        title = (request.data.get("title") or "").strip() or meeting.title
        context_summary = (
            request.data.get("contextSummary")
            or request.data.get("context_summary")
            or ""
        ).strip()
        if not context_summary:
            context_summary = meeting.summary or meeting.objective or meeting.title

        with transaction.atomic():
            project = Project.objects.select_for_update().get(pk=project.pk)
            meeting = (
                Meeting.objects
                .select_for_update()
                .get(pk=meeting.pk, project=project)
            )

            max_seq = (
                Decision.objects.filter(project=project)
                .aggregate(max_seq=Max("project_seq"))
                .get("max_seq")
            )
            next_seq = (max_seq or 0) + 1

            decision = Decision.objects.create(
                title=title,
                context_summary=context_summary,
                author=request.user,
                last_edited_by=request.user,
                project=project,
                project_seq=next_seq,
            )

            MeetingDecisionOrigin.objects.create(
                meeting=meeting,
                decision=decision,
                origin_timestamp=timezone.now(),
                creation_context={
                    "source": "meeting_decisions_endpoint",
                    "meeting_id": meeting.id,
                    "meeting_title": meeting.title,
                    "meeting_summary": meeting.summary,
                    "meeting_objective": meeting.objective,
                    "artifact_links": list(
                        meeting.artifact_links.values(
                            "artifact_type",
                            "artifact_id",
                        )
                    ),
                },
                created_by=request.user,
            )

        serializer = DecisionCommittedSerializer(
            decision,
            context=self.get_serializer_context(),
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        project = self.get_project()
        raw_ids = serializer.validated_data.pop("participant_user_ids", None)
        if raw_ids is None:
            participant_user_ids: list[int] = []
        else:
            participant_user_ids = list(dict.fromkeys(int(x) for x in raw_ids))

        # Strict mode used to require the client to send ids; create flow no longer asks
        # for participants on the form �?default to the creator so the meeting always has
        # at least one participant when the setting is enabled.
        if getattr(settings, "MEETINGS_REQUIRE_PARTICIPANTS_AT_CREATE", False):
            if len(participant_user_ids) < 1:
                participant_user_ids = [self.request.user.id]

        User = get_user_model()

        with transaction.atomic():
            meeting = serializer.save(project=project)

            if not participant_user_ids:
                return

            for uid in participant_user_ids:
                if not User.objects.filter(pk=uid).exists():
                    raise ValidationError(
                        {"participant_user_ids": [f"Unknown user id: {uid}."]}
                    )
                if not ProjectMember.objects.filter(
                    user_id=uid,
                    project=project,
                    is_active=True,
                ).exists():
                    raise ValidationError(
                        {
                            "participant_user_ids": [
                                f"User {uid} is not an active member of this project."
                            ]
                        }
                    )

            for uid in participant_user_ids:
                try:
                    ParticipantLink.objects.get_or_create(
                        meeting=meeting,
                        user_id=uid,
                        defaults={
                            "role": None,
                            # Meeting creator auto-accepts their own participation;
                            # everyone else starts as pending until they respond.
                            "is_accepted": uid == self.request.user.id,
                        },
                    )
                except IntegrityError as exc:
                    raise ValidationError(
                        {
                            "participant_user_ids": [
                                "Could not attach participants; please retry."
                            ]
                        }
                    ) from exc

        if participant_user_ids:
            for uid in participant_user_ids:
                if uid == self.request.user.id:
                    continue
                create_notification(
                    recipient_id=uid,
                    actor_id=self.request.user.id,
                    category=NotificationCategory.MEETINGS,
                    event_type=NotificationEventType.MEETING_CREATED,
                    title=f"New meeting: {meeting.title}",
                    body="You were added as a participant.",
                    related_object_type="meeting",
                    related_object_id=str(meeting.id),
                    action_url=meeting_action_url(meeting.slug, project.id),
                    metadata={"project_id": project.id},
                )

    def perform_update(self, serializer):
        meeting = serializer.instance
        _assert_meeting_not_archived(meeting)
        project = meeting.project

        # Capture before state from DB for notification diffing
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT title, objective, scheduled_date, scheduled_time, type_definition_id, external_reference, layout_config FROM meetings_meeting WHERE id = %s",
                [meeting.pk]
            )
            row = cursor.fetchone()

        if row:
            db_layout_config = row[6]
            if isinstance(db_layout_config, str):
                try:
                    db_layout_config = json.loads(db_layout_config)
                except Exception:
                    pass
            before = {
                "title": row[0],
                "objective": row[1],
                "scheduled_date": str(row[2]) if row[2] else None,
                "scheduled_time": str(row[3]) if row[3] else None,
                "type_definition_id": row[4],
                "external_reference": row[5],
                "layout_config": db_layout_config,
            }
        else:
            before = {
                "title": None, "objective": None, "scheduled_date": None,
                "scheduled_time": None, "type_definition_id": None,
                "external_reference": None, "layout_config": None,
            }

        # Build "after" from INCOMING data for notification diffing (before popping audit fields)
        validated = serializer.validated_data
        after = {
            "title": validated.get("title", before["title"]),
            "objective": validated.get("objective", before["objective"]),
            "scheduled_date": str(validated["scheduled_date"]) if validated.get("scheduled_date") else before["scheduled_date"],
            "scheduled_time": str(validated["scheduled_time"]) if validated.get("scheduled_time") else before["scheduled_time"],
            "type_definition_id": validated.get("type_definition_id", before["type_definition_id"]),
            "external_reference": validated.get("external_reference", before["external_reference"]),
            "layout_config": validated.get("layout_config", before["layout_config"]),
        }

        # Detect minutes publish transition (False → True) before save
        becoming_published = (
            not meeting.minutes_published
            and serializer.validated_data.get("minutes_published") is True
        )

        # Extract fields that need audit service function calls
        validated_data = dict(serializer.validated_data)
        new_title = validated_data.pop("title", None)
        new_objective = validated_data.pop("objective", None)
        new_summary = validated_data.pop("summary", None)
        new_meeting_type_label = validated_data.pop("meeting_type", None)
        new_scheduled_date = validated_data.pop("scheduled_date", None)
        new_scheduled_time = validated_data.pop("scheduled_time", None)
        new_tags = validated_data.pop("tag_definition_ids", None)

        if new_title is not None and new_title != meeting.title:
            update_meeting_title(meeting, new_title, self.request.user)

        if new_objective is not None and new_objective != meeting.objective:
            update_meeting_objective(meeting, new_objective, self.request.user)

        if new_summary is not None and new_summary != meeting.summary:
            update_meeting_summary(meeting, new_summary, self.request.user)

        if new_meeting_type_label is not None:
            new_type_obj = ensure_meeting_type_definition(meeting.project, new_meeting_type_label)
            if new_type_obj != meeting.type_definition:
                update_meeting_type(meeting, new_type_obj, self.request.user)

        date_changed = (
            new_scheduled_date is not None and new_scheduled_date != meeting.scheduled_date
        ) or (
            new_scheduled_time is not None and new_scheduled_time != meeting.scheduled_time
        )
        if date_changed:
            update_meeting_datetime(
                meeting,
                new_scheduled_date if new_scheduled_date is not None else meeting.scheduled_date,
                new_scheduled_time if new_scheduled_time is not None else meeting.scheduled_time,
                self.request.user,
            )

        if new_tags is not None:
            update_meeting_tags(meeting, new_tags, self.request.user)

        # Save remaining fields via serializer
        if validated_data:
            serializer._validated_data = validated_data
            serializer.save()

        # Meeting minutes published notification
        if becoming_published:
            participant_ids = list(
                meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
            )
            for uid in participant_ids:
                if uid == self.request.user.id:
                    continue
                create_notification(
                    recipient_id=uid,
                    actor_id=self.request.user.id,
                    category=NotificationCategory.MEETINGS,
                    event_type=NotificationEventType.MEETING_MINUTES_PUBLISHED,
                    title=f"Meeting minutes published: {meeting.title}",
                    body="The meeting minutes have been published.",
                    related_object_type="meeting",
                    related_object_id=str(meeting.id),
                    action_url=meeting_action_url(meeting.slug, meeting.project_id),
                    metadata={
                        "project_id": meeting.project_id,
                        "meeting_title": meeting.title,
                        "change_type": "minutes_published",
                    },
                )

        # Check if anything changed for notification
        if json.dumps(before, sort_keys=True, default=str) == json.dumps(after, sort_keys=True, default=str):
            return

        def extract_agenda_items(layout_config):
            if not layout_config or not isinstance(layout_config, dict):
                return {}
            nested_sections = layout_config.get("nestedSections", [])
            if not isinstance(nested_sections, list):
                return {}
            items = {}
            for section in nested_sections:
                if not isinstance(section, dict):
                    continue
                section_title = section.get("title", "").strip()
                for item in section.get("items", []):
                    if isinstance(item, dict):
                        item_id = item.get("id")
                        item_text = item.get("text", "").strip()
                        if item_id:
                            items[str(item_id)] = {"section": section_title, "text": item_text}
            return items

        def extract_sections(layout_config):
            if not layout_config or not isinstance(layout_config, dict):
                return {}
            nested = layout_config.get("nestedSections", [])
            if not isinstance(nested, list):
                return {}
            result = {}
            for section in nested:
                if isinstance(section, dict):
                    sec_id = section.get("id")
                    if sec_id:
                        result[str(sec_id)] = section.get("title", "").strip()
            return result

        changes = {}
        if before["scheduled_date"] != after["scheduled_date"] or before["scheduled_time"] != after["scheduled_time"]:
            old_time = None
            new_time = None
            if before["scheduled_date"]:
                old_time = before["scheduled_date"]
                if before["scheduled_time"]:
                    old_time += f" {before['scheduled_time']}"
            if after["scheduled_date"]:
                new_time = after["scheduled_date"]
                if after["scheduled_time"]:
                    new_time += f" {after['scheduled_time']}"
            changes["old_time"] = old_time
            changes["new_time"] = new_time
        if before["objective"] != after["objective"]:
            changes["old_agenda"] = before["objective"]
            changes["new_agenda"] = after["objective"]
        if before["external_reference"] != after["external_reference"]:
            changes["old_location"] = before["external_reference"]
            changes["new_location"] = after["external_reference"]
        if before["title"] != after["title"]:
            changes["old_title"] = before["title"]
            changes["new_title"] = after["title"]

        old_layout = before["layout_config"]
        new_layout = after["layout_config"]
        if isinstance(old_layout, str):
            try:
                old_layout = json.loads(old_layout)
            except (json.JSONDecodeError, TypeError):
                old_layout = None
        if isinstance(new_layout, str):
            try:
                new_layout = json.loads(new_layout)
            except (json.JSONDecodeError, TypeError):
                new_layout = None

        old_items = extract_agenda_items(old_layout)
        new_items = extract_agenda_items(new_layout)
        old_item_ids = set(old_items.keys())
        new_item_ids = set(new_items.keys())
        common_ids = old_item_ids & new_item_ids

        section_changes = []
        for item_id in common_ids:
            old_section = old_items[item_id]["section"]
            new_section = new_items[item_id]["section"]
            if old_section.strip() != new_section.strip():
                section_changes.append({
                    "id": item_id,
                    "old_section": old_section.strip(),
                    "new_section": new_section.strip(),
                })

        old_sections_map = extract_sections(old_layout)
        new_sections_map = extract_sections(new_layout)
        already_seen_old_titles = {sc["old_section"] for sc in section_changes}
        for sec_id in old_sections_map.keys() & new_sections_map.keys():
            old_t = old_sections_map[sec_id]
            new_t = new_sections_map[sec_id]
            if old_t != new_t and old_t not in already_seen_old_titles:
                section_changes.append({
                    "id": f"section:{sec_id}",
                    "old_section": old_t,
                    "new_section": new_t,
                })

        if section_changes:
            first_sec = section_changes[0]
            changes["old_agenda"] = first_sec["old_section"]
            changes["new_agenda"] = first_sec["new_section"]
            changes["change_type"] = "agenda_section"

        if not changes:
            return

        participant_ids = meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
        for uid in participant_ids:
            if uid == self.request.user.id:
                continue
            create_notification(
                recipient_id=uid,
                actor_id=self.request.user.id,
                category=NotificationCategory.MEETINGS,
                event_type=NotificationEventType.MEETING_UPDATED,
                title=f"Meeting updated: {meeting.title}",
                body="Meeting details were changed.",
                related_object_type="meeting",
                related_object_id=str(meeting.id),
                action_url=meeting_action_url(meeting.slug, project.id),
                metadata={"project_id": project.id, **changes},
            )


    def destroy(self, request, *args, **kwargs):
        """
        Deleting a meeting is blocked when it has converted action items, because
        tasks keep immutable lineage via Task.origin_action_item (PROTECT).
        Return a clear API error instead of a 500.
        """
        _assert_meeting_not_archived(self.get_object())
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    "detail": "This meeting cannot be deleted because it has action items that were converted into tasks. Delete or unlink those tasks first."
                },
                status=status.HTTP_409_CONFLICT,
            )

    @action(detail=True, methods=["get"], url_path="tasks")
    def meeting_tasks(self, request, project_id=None, pk=None):
        """Tasks anchored to this meeting via ``MeetingTaskOrigin`` (paginated)."""
        meeting = self.get_object()
        _ensure_project_membership(request.user, meeting.project)
        from meetings.models import Meeting, MeetingTaskOrigin
        from task.models import Task
        from task.serializers import TaskListSerializer

        task_ids = MeetingTaskOrigin.objects.filter(meeting=meeting).values_list(
            "task_id", flat=True
        )
        qs = (
            Task.objects.filter(id__in=task_ids, project_id=meeting.project_id)
            .select_related("owner", "project")
            .order_by("-id")
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            ser = TaskListSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(ser.data)
        ser = TaskListSerializer(qs, many=True, context={"request": request})
        return Response(ser.data)

    @action(detail=True, methods=["get"], url_path="lifecycle")
    def lifecycle(self, request, project_id=None, pk=None):
        """Return current lifecycle state and available transitions."""
        meeting = self.get_object()
        data = {
            "status": meeting.status,
            "available_transitions": get_available_transitions(meeting),
        }
        serializer = MeetingLifecycleSerializer(data)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="lifecycle/transition")
    def transition(self, request, project_id=None, pk=None):
        """Transition meeting status via state machine (validates rules, then audits)."""
        meeting = self.get_object()

        serializer = TransitionRequestSerializer(data=request.data, context={'meeting': meeting})
        serializer.is_valid(raise_exception=True)

        to_state = serializer.validated_data["to_state"]
        old_status = meeting.status

        # Step 1: Execute transition (validates state machine rules, saves meeting)
        updated = execute_transition(meeting, to_state)

        # Step 2: Record audit entry (after successful transition)
        from meetings.audit import record_audit_entry
        record_audit_entry(
            meeting=updated,
            actor=request.user,
            event_type='meeting.status_changed',
            before={'status': old_status},
            after={'status': to_state},
            context={'reason': 'api_request'},
        )

        # Return updated meeting state with available transitions
        return Response(
            {
                "status": updated.status,
                "available_transitions": get_available_transitions(updated),
            },
            status=status.HTTP_200_OK,
        )


class AgendaItemViewSet(ArchivedMeetingGuardMixin, viewsets.ModelViewSet):
    serializer_class = AgendaItemSerializer
    permission_classes = [IsAuthenticated]

    def get_meeting(self) -> Meeting:
        project_id = self.kwargs.get("project_id")
        meeting_id = self.kwargs.get("meeting_id")
        meeting = get_object_or_404(
            Meeting.objects.select_related("project"),
            **resolve_lookup_kwargs(meeting_id, 'id'),
            **resolve_lookup_kwargs(project_id, 'project_id', 'project__slug'),
        )
        _ensure_project_membership(self.request.user, meeting.project)
        return meeting

    def get_queryset(self):
        meeting = self.get_meeting()
        return meeting.agenda_items.all().order_by("order_index", "id")

    def perform_create(self, serializer):
        meeting = self.get_meeting()
        vd = serializer.validated_data
        try:
            agenda_item = create_agenda_item(
                meeting=meeting,
                content=vd.get("content", ""),
                order_index=vd.get("order_index", 0),
                is_priority=vd.get("is_priority", False),
                actor=self.request.user,
            )
            serializer.instance = agenda_item
        except IntegrityError:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                {"order_index": ["This order_index is already used for this meeting."]}
            )

        actor_id = self.request.user.id
        participant_ids = list(meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True))
        recipients = [uid for uid in participant_ids if uid != actor_id]
        logger.info(
            "AgendaItem CREATE: meeting=%s item=%s actor=%s participants=%s recipients=%s",
            meeting.id, agenda_item.pk, actor_id, participant_ids, recipients,
        )
        for uid in recipients:
            notify_agenda_event(
                meeting=meeting,
                actor_id=actor_id,
                recipient_id=uid,
                change_type="agenda_item_create",
                item_id=agenda_item.pk,
                new_content=agenda_item.content or "",
            )

    def perform_update(self, serializer):
        agenda_item = serializer.instance
        meeting = agenda_item.meeting
        vd = serializer.validated_data

        old_content = normalize_text(agenda_item.content or "")

        update_agenda_item(
            item=agenda_item,
            content=vd.get("content", agenda_item.content),
            is_priority=vd.get("is_priority", agenda_item.is_priority),
            actor=self.request.user,
        )

        new_content = normalize_text(agenda_item.content)
        if old_content == new_content:
            return

        actor_id = self.request.user.id
        participant_ids = list(
            meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
        )
        recipients = [uid for uid in participant_ids if uid != actor_id]
        logger.info(
            "AgendaItem UPDATE: meeting=%s item=%s actor=%s participants=%s recipients=%s",
            meeting.id, agenda_item.pk, actor_id, participant_ids, recipients,
        )
        for uid in recipients:
            upsert_agenda_item_notification(
                meeting=meeting,
                item_id=agenda_item.pk,
                old_content=old_content,
                new_content=new_content,
                actor_id=actor_id,
                recipient_id=uid,
            )

    def perform_destroy(self, instance):
        meeting = instance.meeting
        removed_content = instance.content
        item_pk = instance.pk

        delete_agenda_item(item=instance, actor=self.request.user)

        actor_id = self.request.user.id
        participant_ids = list(
            meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
        )
        recipients = [uid for uid in participant_ids if uid != actor_id]
        logger.info(
            "AgendaItem DELETE: meeting=%s item=%s actor=%s participants=%s recipients=%s",
            meeting.id, item_pk, actor_id, participant_ids, recipients,
        )
        for uid in recipients:
            notify_agenda_event(
                meeting=meeting,
                actor_id=actor_id,
                recipient_id=uid,
                change_type="agenda_item_delete",
                item_id=item_pk,
                old_content=removed_content or "",
            )

    @action(detail=False, methods=["patch"], url_path="reorder")
    def reorder(self, request, project_id=None, meeting_id=None):
        meeting = self.get_meeting()
        _assert_meeting_not_archived(meeting)
        items = request.data.get("items", [])
        if not isinstance(items, list):
            return Response(
                {"items": ["This field must be a list of objects."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalized = []
        for item in items:
            try:
                normalized.append(
                    {
                        "id": int(item["id"]),
                        "order_index": int(item["order_index"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                return Response(
                    {
                        "items": [
                            "Each item must contain integer 'id' and 'order_index'."
                        ]
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        updated_items = reorder_agenda_items(meeting.id, normalized)
        serializer = self.get_serializer(updated_items, many=True)
        return Response(serializer.data)


class ParticipantLinkViewSet(ArchivedMeetingGuardMixin, viewsets.ModelViewSet):
    serializer_class = ParticipantLinkSerializer
    permission_classes = [IsAuthenticated]

    def get_meeting(self) -> Meeting:
        project_id = self.kwargs.get("project_id")
        meeting_id = self.kwargs.get("meeting_id")
        meeting = get_object_or_404(
            Meeting.objects.select_related("project"),
            **resolve_lookup_kwargs(meeting_id, 'id'),
            **resolve_lookup_kwargs(project_id, 'project_id', 'project__slug'),
        )
        _ensure_project_membership(self.request.user, meeting.project)
        return meeting

    def get_queryset(self):
        meeting = self.get_meeting()
        return meeting.participant_links.all()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in {"create", "update", "partial_update"}:
            context["meeting"] = self.get_meeting()
        return context

    def perform_create(self, serializer):
        meeting = self.get_meeting()
        invited_user = serializer.validated_data.get("user")
        role = serializer.validated_data.get("role")
        is_self = bool(invited_user and invited_user.pk == self.request.user.id)

        link = add_participant(
            meeting=meeting,
            user=invited_user,
            role=role,
            actor=self.request.user,
        )

        if is_self and not link.is_accepted:
            link.is_accepted = True
            link.save(update_fields=['is_accepted'])

        if link.user_id != self.request.user.id:
            create_notification(
                recipient_id=link.user_id,
                actor_id=self.request.user.id,
                category=NotificationCategory.MEETINGS,
                event_type=NotificationEventType.MEETING_PARTICIPANT_ADDED,
                title=f"Added to meeting: {meeting.title}",
                body="You were added as a participant.",
                related_object_type="meeting",
                related_object_id=str(meeting.id),
                action_url=meeting_action_url(meeting.slug, meeting.project_id),
                metadata={
                    "project_id": meeting.project_id,
                    "meeting_title": meeting.title,
                    "participant_link_id": link.pk,
                },
            )

    def perform_destroy(self, instance):
        meeting = self.get_meeting()
        user = instance.user
        removed_user_id = instance.user_id

        remove_participant(
            meeting=meeting,
            user=user,
            actor=self.request.user,
        )

        if removed_user_id != self.request.user.id:
            create_notification(
                recipient_id=removed_user_id,
                actor_id=self.request.user.id,
                category=NotificationCategory.MEETINGS,
                event_type=NotificationEventType.MEETING_PARTICIPANT_REMOVED,
                title=f"Removed from meeting: {meeting.title}",
                body="You were removed from this meeting.",
                related_object_type="meeting",
                related_object_id=str(meeting.id),
                action_url=meeting_action_url(meeting.slug, meeting.project_id),
                metadata={
                    "meeting_title": meeting.title,
                    "project_name": meeting.project.name,
                    "project_id": meeting.project_id,
                    "revoked_access": True,
                },
            )
            from notifications.services import revoke_access_to_resource
            revoke_access_to_resource(
                user_id=removed_user_id,
                object_type="meeting",
                object_id=str(meeting.id)
            )


class ArtifactLinkViewSet(ArchivedMeetingGuardMixin, viewsets.ModelViewSet):
    serializer_class = ArtifactLinkSerializer
    permission_classes = [IsAuthenticated]

    def get_meeting(self) -> Meeting:
        project_id = self.kwargs.get("project_id")
        meeting_id = self.kwargs.get("meeting_id")
        meeting = get_object_or_404(
            Meeting.objects.select_related("project"),
            **resolve_lookup_kwargs(meeting_id, 'id'),
            **resolve_lookup_kwargs(project_id, 'project_id', 'project__slug'),
        )
        _ensure_project_membership(self.request.user, meeting.project)
        return meeting

    def get_queryset(self):
        meeting = self.get_meeting()
        return meeting.artifact_links.all()

    @staticmethod
    def _resolve_artifact_title(artifact_type: str, artifact_id: int) -> str:
        """Look up a human-readable title for the linked artifact."""
        try:
            if artifact_type == "decision":
                from decision.models import Decision  # noqa: PLC0415
                d = Decision.objects.only("title").get(pk=artifact_id)
                return d.title or f"Decision #{artifact_id}"
            if artifact_type == "task":
                from task.models import Task  # noqa: PLC0415
                t = Task.objects.only("summary").get(pk=artifact_id)
                return t.summary or f"Task #{artifact_id}"
            if artifact_type == "spreadsheet":
                from spreadsheet.models import Spreadsheet  # noqa: PLC0415
                s = Spreadsheet.objects.only("name").get(pk=artifact_id)
                return s.name or f"Spreadsheet #{artifact_id}"
        except Exception:
            pass
        return f"{artifact_type.capitalize()} #{artifact_id}"

    def perform_create(self, serializer):
        meeting = self.get_meeting()
        artifact = serializer.save(meeting=meeting)

        artifact_type = artifact.artifact_type
        artifact_id = artifact.artifact_id
        artifact_title = self._resolve_artifact_title(artifact_type, artifact_id)
        type_label = artifact_type.capitalize()

        participant_ids = list(
            meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
        )
        for uid in participant_ids:
            if uid == self.request.user.id:
                continue
            create_notification(
                recipient_id=uid,
                actor_id=self.request.user.id,
                category=NotificationCategory.MEETINGS,
                event_type=NotificationEventType.MEETING_UPDATED,
                title=f"{type_label} linked to meeting: {meeting.title}",
                body=f"{type_label} \"{artifact_title}\" was linked to this meeting.",
                related_object_type="meeting",
                related_object_id=str(meeting.id),
                action_url=meeting_action_url(meeting.slug, meeting.project_id),
                metadata={
                    "project_id": meeting.project_id,
                    "change_type": "artifact_linked",
                    "artifact_type": artifact_type,
                    "artifact_id": artifact_id,
                    "artifact_title": artifact_title,
                },
            )

    def perform_destroy(self, instance):
        meeting = instance.meeting
        artifact_type = instance.artifact_type
        artifact_id = instance.artifact_id
        artifact_title = self._resolve_artifact_title(artifact_type, artifact_id)
        type_label = artifact_type.capitalize()

        instance.delete()

        participant_ids = list(
            meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
        )
        for uid in participant_ids:
            if uid == self.request.user.id:
                continue
            create_notification(
                recipient_id=uid,
                actor_id=self.request.user.id,
                category=NotificationCategory.MEETINGS,
                event_type=NotificationEventType.MEETING_UPDATED,
                title=f"{type_label} unlinked from meeting: {meeting.title}",
                body=f"{type_label} \"{artifact_title}\" was removed from this meeting.",
                related_object_type="meeting",
                related_object_id=str(meeting.id),
                action_url=meeting_action_url(meeting.slug, meeting.project_id),
                metadata={
                    "project_id": meeting.project_id,
                    "change_type": "artifact_unlinked",
                    "artifact_type": artifact_type,
                    "artifact_id": artifact_id,
                    "artifact_title": artifact_title,
                },
            )


class MeetingActionItemViewSet(ArchivedMeetingGuardMixin, viewsets.ModelViewSet):
    """
    Meeting follow-up action items and conversion to executable tasks (SMP-489).
    """

    serializer_class = MeetingActionItemSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_meeting(self) -> Meeting:
        project_id = self.kwargs.get("project_id")
        meeting_id = self.kwargs.get("meeting_id")
        meeting = get_object_or_404(
            Meeting.objects.select_related("project"),
            **resolve_lookup_kwargs(meeting_id, 'id'),
            **resolve_lookup_kwargs(project_id, 'project_id', 'project__slug'),
        )
        _ensure_project_membership(self.request.user, meeting.project)
        return meeting

    def get_queryset(self):
        meeting = self.get_meeting()
        return meeting.action_items.all().order_by("order_index", "id")

    def perform_create(self, serializer):
        meeting = self.get_meeting()
        vd = serializer.validated_data
        serializer.instance = create_action_item(
            meeting=meeting,
            title=vd.get("title", ""),
            description=vd.get("description", ""),
            order_index=vd.get("order_index", 0),
            actor=self.request.user,
        )

    def perform_update(self, serializer):
        vd = serializer.validated_data
        update_action_item(
            item=serializer.instance,
            title=vd.get("title", serializer.instance.title),
            description=vd.get("description", serializer.instance.description),
            is_resolved=vd.get("is_resolved", serializer.instance.is_resolved),
            actor=self.request.user,
        )

    @action(detail=True, methods=["post"], url_path="convert-to-task")
    def convert_to_task(self, request, project_id=None, meeting_id=None, pk=None):
        from meetings.action_item_tasks import convert_meeting_action_item_to_task
        from task.serializers import TaskSerializer

        meeting = self.get_meeting()
        _assert_meeting_not_archived(meeting)
        action_item = get_object_or_404(
            MeetingActionItem,
            pk=pk,
            meeting_id=meeting.id,
        )
        sz = ActionItemConvertSerializer(data=request.data)
        sz.is_valid(raise_exception=True)
        vd = sz.validated_data
        task = convert_meeting_action_item_to_task(
            user=request.user,
            meeting=meeting,
            action_item=action_item,
            owner_id=vd.get("owner_id"),
            due_date=vd.get("due_date"),
            priority=vd.get("priority"),
            task_type=vd.get("type", "execution"),
            current_approver_id=vd.get("current_approver_id"),
            create_as_draft=vd.get("create_as_draft", False),
        )
        return Response(
            TaskSerializer(task, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="bulk-convert-to-task")
    def bulk_convert_to_task(self, request, project_id=None, meeting_id=None):
        from meetings.action_item_tasks import bulk_convert_meeting_action_items
        from task.serializers import TaskSerializer

        meeting = self.get_meeting()
        _assert_meeting_not_archived(meeting)
        sz = BulkActionItemConvertSerializer(data=request.data)
        sz.is_valid(raise_exception=True)
        items = [dict(x) for x in sz.validated_data["items"]]
        tasks = bulk_convert_meeting_action_items(
            user=request.user,
            meeting=meeting,
            items=items,
        )
        data = TaskSerializer(tasks, many=True, context={"request": request}).data
        return Response({"tasks": data}, status=status.HTTP_201_CREATED)

    # Backwards-compat / typo-tolerance: some clients hit ".../bulk-convert-to-tasks/" (plural).
    @action(detail=False, methods=["post"], url_path="bulk-convert-to-tasks")
    def bulk_convert_to_tasks(self, request, project_id=None, meeting_id=None):
        return self.bulk_convert_to_task(request, project_id=project_id, meeting_id=meeting_id)


class MeetingDocumentAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_meeting(self, project_id: int, meeting_id: int) -> Meeting:
        meeting = get_object_or_404(
            Meeting.objects.select_related("project"),
            **resolve_lookup_kwargs(meeting_id, 'id'),
            **resolve_lookup_kwargs(project_id, 'project_id', 'project__slug'),
        )
        _ensure_meeting_document_access(self.request.user, meeting)
        return meeting

    def get(self, request, project_id: int, meeting_id: int):
        meeting = self._get_meeting(project_id, meeting_id)
        document = get_or_create_meeting_document(meeting.id)
        serializer = MeetingDocumentSerializer(document)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, project_id: int, meeting_id: int):
        meeting = self._get_meeting(project_id, meeting_id)
        _assert_meeting_not_archived(meeting)
        content = request.data.get("content")
        if not isinstance(content, str):
            raise ValidationError({"content": ["This field is required and must be a string."]})
        yjs_state = request.data.get("yjs_state")
        if yjs_state is not None and not isinstance(yjs_state, str):
            raise ValidationError({"yjs_state": ["This field must be a string."]})
        document = update_meeting_document_content(
            meeting_id=meeting.id,
            content=content,
            yjs_state=yjs_state,
            user_id=request.user.id,
            notify_collaborators=True,
        )
        from meetings.audit import record_audit_entry
        record_audit_entry(
            meeting=meeting,
            actor=request.user,
            event_type='meeting.document_edited',
            context={'char_count_after': len(content)},
        )
        if isinstance(yjs_state, str):
            document.yjs_state = yjs_state
            document.save(update_fields=['yjs_state'])
        serializer = MeetingDocumentSerializer(document)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MeetingAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MeetingAuditLogSerializer
    pagination_class = AuditLogPagination
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['timestamp']
    ordering = ['-timestamp']

    def get_queryset(self):
        meeting_id = self.kwargs.get('meeting_id')
        project_id = self.kwargs.get('project_id')

        # Verify project membership
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))
        _ensure_project_membership(self.request.user, project)

        # Get meeting and verify it belongs to project
        meeting = get_object_or_404(Meeting, **resolve_lookup_kwargs(meeting_id, 'id'), project=project)

        queryset = MeetingAuditLog.objects.filter(meeting=meeting).select_related('actor')

        # Handle event_type filtering (multi-value with OR logic)
        event_types = self.request.query_params.getlist('event_type')
        if event_types:
            valid_types = [choice[0] for choice in MeetingAuditLog.EVENT_TYPE_CHOICES]
            for event_type in event_types:
                if event_type not in valid_types:
                    raise ValidationError({
                        'event_type': f'Invalid event type. Must be one of: {", ".join(valid_types)}'
                    })
            queryset = queryset.filter(Q(event_type__in=event_types))

        # Handle actor_id filtering
        actor_id = self.request.query_params.get('actor_id')
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)

        # Handle date range filtering
        from_date = self.request.query_params.get('from')
        to_date = self.request.query_params.get('to')

        if from_date:
            from_datetime = parse_datetime(from_date.replace(' ', '+').replace('Z', '+00:00'))
            if from_datetime is None:
                raise ValidationError({'from': 'Invalid ISO 8601 date format'})
            queryset = queryset.filter(timestamp__gte=from_datetime)

        if to_date:
            to_datetime = parse_datetime(to_date.replace(' ', '+').replace('Z', '+00:00'))
            if to_datetime is None:
                raise ValidationError({'to': 'Invalid ISO 8601 date format'})
            queryset = queryset.filter(timestamp__lte=to_datetime)

        return queryset

