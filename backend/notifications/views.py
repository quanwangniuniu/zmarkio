import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .sse import _allow_replay_attempt, sse_event_generator

logger = logging.getLogger(__name__)

from .models import (
    DEADLINE_TAB_EVENT_TYPES,
    MENTION_TAB_EVENT_TYPES,
    Notification,
    NotificationEventType,
    UserNotificationPreference,
    default_notification_preferences,
)
from .serializers import (
    NotificationClearSerializer,
    NotificationMarkReadSerializer,
    NotificationRespondSerializer,
    NotificationSerializer,
    UserNotificationPreferencePatchSerializer,
)
from .services import (
    apply_and_save_notification_preferences,
    clear_notifications,
    coalesce_preferences,
    create_notification,
    filter_notifications_for_user,
    mark_notifications_read,
    repair_duplicate_chat_notifications_for_user,
    repair_stale_chat_notifications_for_user,
)


def _calculate_unread_count(user) -> int:
    """
    Calculate the total unread count for a user.

    For chat notifications (chat_new_message), we count the message_count from metadata
    instead of counting as 1, so the bell badge reflects actual unread messages.
    """
    unread_notifications = Notification.objects.filter(recipient=user, is_read=False)
    total = 0
    for n in unread_notifications:
        if n.event_type == NotificationEventType.CHAT_NEW_MESSAGE:
            # Use message_count from metadata, default to 1 if not present
            total += n.metadata.get("message_count", 1) if n.metadata else 1
        else:
            total += 1
    return total


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).select_related("actor")

    def list(self, request, *args, **kwargs):
        repair_stale_chat_notifications_for_user(request.user)
        repair_duplicate_chat_notifications_for_user(request.user)

        qs = self.get_queryset()
        category = request.query_params.get("category")
        is_read_param = request.query_params.get("is_read")
        tab = request.query_params.get("tab", "all")

        is_read = None
        if is_read_param is not None:
            if is_read_param.lower() in ("true", "1", "yes"):
                is_read = True
            elif is_read_param.lower() in ("false", "0", "no"):
                is_read = False

        qs = filter_notifications_for_user(
            qs,
            category=category or None,
            is_read=is_read,
            tab=tab if tab != "all" else None,
        )

        page = self.paginate_queryset(qs)
        unread_total = _calculate_unread_count(request.user)

        tab_counts = {
            "all": Notification.objects.filter(recipient=request.user).count(),
            "unread": unread_total,
            "mentions": Notification.objects.filter(
                recipient=request.user,
                event_type__in=MENTION_TAB_EVENT_TYPES,
            ).count(),
            "deadlines": Notification.objects.filter(
                recipient=request.user,
                event_type__in=DEADLINE_TAB_EVENT_TYPES,
            ).count(),
        }

        if page is not None:
            ser = self.get_serializer(page, many=True)
            response = self.get_paginated_response(ser.data)
            response.data["unread_count"] = unread_total
            response.data["tab_counts"] = tab_counts
            return response

        ser = self.get_serializer(qs, many=True)
        return Response(
            {
                "results": ser.data,
                "unread_count": unread_total,
                "tab_counts": tab_counts,
            }
        )


class NotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        ser = NotificationMarkReadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        mark_all = ser.validated_data.get("mark_all", False)
        ids = [str(x) for x in ser.validated_data.get("ids") or []]
        updated = mark_notifications_read(request.user, ids, mark_all=mark_all)
        return Response({"updated": updated})


class NotificationClearView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        ser = NotificationClearSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        scope = ser.validated_data["scope"]
        ids = [str(x) for x in ser.validated_data.get("ids") or []]
        deleted = clear_notifications(request.user, scope=scope, ids=ids)
        return Response({"deleted": deleted})


class UserNotificationPreferenceView(APIView):
    """
    Use JWT only so browser PATCH requests are not subject to SessionAuthentication CSRF checks.
    """

    authentication_classes = [JWTAuthentication]
    parser_classes = [JSONParser]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        Always return coalesced defaults when no row exists or when preferences JSON is empty.
        On unexpected errors (e.g. DB), still return default-shaped preferences so the client can render.
        """
        try:
            pref, _ = UserNotificationPreference.objects.get_or_create(
                user=request.user,
                defaults={"preferences": default_notification_preferences()},
            )
            merged = coalesce_preferences(pref.preferences)
            return Response({"preferences": merged, "updated_at": pref.updated_at})
        except Exception:
            logger.exception("notification-preferences GET failed; returning default preferences")
            merged = coalesce_preferences(None)
            return Response({"preferences": merged, "updated_at": None})

    def patch(self, request):
        ser = UserNotificationPreferencePatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        patch_data = ser.validated_data["preferences"]
        try:
            pref = apply_and_save_notification_preferences(request.user, patch_data)
        except Exception as exc:
            logger.exception(
                "notification-preferences PATCH failed: user_id=%s",
                getattr(request.user, "pk", None),
            )
            return Response(
                {
                    "error": "Failed to save notification preferences.",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        merged = coalesce_preferences(pref.preferences)
        return Response({"preferences": merged, "updated_at": pref.updated_at})


class NotificationRespondView(APIView):
    """
    POST /api/notifications/{pk}/respond/
    Body: {"action": "accept" | "reject"}

    Lets the notification recipient accept or decline an actionable notification
    (project invite, meeting participant addition, task assignment / approver change).
    After responding the actor receives a follow-up notification.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)

        ser = NotificationRespondSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        action = ser.validated_data["action"]

        # Clicking Pick a time used to mark the invite accepted. A guest who
        # never booked must still be able to decline.
        meta = notification.metadata or {}
        late_decline = (
            action == "reject"
            and (notification.related_object_type or "").lower() == "booking_link"
            and not meta.get("booked")
        )
        if notification.responded and not late_decline:
            return Response(
                {"detail": "You have already responded to this notification."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Dispatch side-effects
        try:
            _dispatch_response(notification, action, request.user)
        except Exception:
            logger.exception(
                "Failed to execute respond action=%s for notification %s", action, pk
            )
            return Response(
                {"detail": "Action failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        notification.responded = True
        notification.response = action
        notification.save(update_fields=["responded", "response"])

        # Notify the original actor about the response
        if notification.actor_id:
            user = request.user
            user_display = user.get_full_name() or user.username
            action_label = "accepted" if action == "accept" else "declined"
            _send_response_notification(notification, user, user_display, action_label)

        return Response({"status": "ok", "action": action})


def _send_response_notification(notification, responder, responder_display, action_label):
    """Send a follow-up notification to the original actor."""
    from notifications.models import NotificationCategory  # noqa: PLC0415

    # Reuse the same category / event_type so the actor sees it in context
    # Filter out change-related metadata fields that should not appear in response notifications
    filtered_metadata = {
        k: v
        for k, v in notification.metadata.items()
        if k not in ("change_type", "old_value", "new_value", "old_status", "new_status")
    }

    try:
        create_notification(
            recipient_id=notification.actor_id,
            actor_id=responder.id,
            category=notification.category,
            event_type=notification.event_type,
            title=f"{responder_display} {action_label} your invitation",
            body=f"{responder_display} has {action_label} your request.",
            related_object_type=notification.related_object_type,
            related_object_id=notification.related_object_id,
            action_url=notification.action_url,
            metadata={
                **filtered_metadata,
                "response": action_label,
                "responder": responder_display,
                "is_response_feedback": True,
            },
        )
    except Exception:
        logger.exception("Failed to send response notification to actor %s", notification.actor_id)


def _dispatch_response(notification, action, user):
    """Apply domain-level side effects for accept/reject."""
    et = notification.event_type

    if et == NotificationEventType.PROJECT_INVITE:
        _handle_project_invite(notification, action, user)
    elif et == NotificationEventType.ORG_INVITE:
        _handle_org_invite(notification, action, user)
    elif et == NotificationEventType.MEETING_PARTICIPANT_ADDED:
        _handle_meeting_participant(notification, action, user)
    elif et in (NotificationEventType.TASK_ASSIGNED, NotificationEventType.TASK_OWNER_CHANGED):
        _handle_task_assignment(notification, action, user)


def _handle_project_invite(notification, action, user):
    """Accept → activate/create ProjectMember; reject → revoke access to project notifications."""
    if action == "reject":
        # User rejected the invitation - revoke access to all historical project notifications
        from notifications.services import revoke_access_to_resource  # noqa: PLC0415
        revoke_access_to_resource(
            user_id=user.id,
            object_type=notification.related_object_type,
            object_id=notification.related_object_id
        )
        return
    if action != "accept":
        return
    from django.db import transaction  # noqa: PLC0415
    from django.utils import timezone as tz  # noqa: PLC0415
    from core.models import ProjectMember, ProjectInvitation  # noqa: PLC0415
    from core.utils.project_calendars import ensure_project_calendar  # noqa: PLC0415

    invitation_id = notification.metadata.get("invitation_id")
    if not invitation_id:
        logger.warning("PROJECT_INVITE notification %s has no invitation_id in metadata", notification.pk)
        return

    try:
        inv = ProjectInvitation.objects.select_related("project").get(pk=invitation_id)
    except ProjectInvitation.DoesNotExist:
        logger.warning("ProjectInvitation %s not found", invitation_id)
        return

    with transaction.atomic():
        ProjectMember.objects.update_or_create(
            user=user,
            project=inv.project,
            defaults={"role": inv.role or "member", "is_active": True},
        )
        ensure_project_calendar(inv.project)

        # Align with token accept path: drop stale accepted rows before unique constraint.
        ProjectInvitation.objects.filter(
            email=inv.email,
            project=inv.project,
            accepted=True,
        ).exclude(id=inv.id).delete()

        if not inv.accepted:
            inv.accepted = True
            inv.accepted_at = tz.now()
            inv.save(update_fields=["accepted", "accepted_at"])


def _handle_org_invite(notification, action, user):
    """
    Accept → create/activate OrganizationMembership; update invitation use count.
    Reject → deactivate the OrganizationInvitation.
    """
    from django.db import transaction  # noqa: PLC0415
    from core.models import Organization, OrganizationMembership, OrganizationInvitation  # noqa: PLC0415

    invitation_id = notification.metadata.get("invitation_id")
    org_id = notification.related_object_id
    role = notification.metadata.get("role", "member")

    if action == "reject":
        if invitation_id:
            OrganizationInvitation.objects.filter(pk=invitation_id, is_active=True).update(
                is_active=False
            )
        return

    if action != "accept":
        return

    try:
        org = Organization.objects.get(pk=org_id)
    except Organization.DoesNotExist:
        logger.warning("Organization %s not found for org_invite response", org_id)
        return

    with transaction.atomic():
        OrganizationMembership.objects.update_or_create(
            user=user,
            organization=org,
            defaults={"role": role, "is_active": True},
        )
        if invitation_id:
            try:
                inv = OrganizationInvitation.objects.get(pk=invitation_id)
                inv.use_count = (inv.use_count or 0) + 1
                if inv.max_uses > 0 and inv.use_count >= inv.max_uses:
                    inv.is_active = False
                inv.save(update_fields=["use_count", "is_active"])
            except OrganizationInvitation.DoesNotExist:
                pass


def _handle_booking_link_invite(notification, action, user):
    """Named guest: booking the slot is the accept. Decline removes them from the link."""
    from calendars.models import BookingLink  # noqa: PLC0415

    link_id = notification.related_object_id
    try:
        link = BookingLink.objects.filter(is_deleted=False).get(pk=link_id)
    except (BookingLink.DoesNotExist, ValueError, TypeError):
        logger.warning("BookingLink %s not found for invite response", link_id)
        return

    if action == "reject":
        link.invitee_users.remove(user)


def _handle_meeting_participant(notification, action, user):
    """Accept → mark participant link as accepted; reject → remove the link and revoke access."""
    if (notification.related_object_type or "").lower() == "booking_link":
        _handle_booking_link_invite(notification, action, user)
        return

    from meetings.models import ParticipantLink  # noqa: PLC0415

    participant_link_id = notification.metadata.get("participant_link_id")
    if not participant_link_id:
        logger.warning("MEETING_PARTICIPANT_ADDED notification %s has no participant_link_id", notification.pk)
        return

    if action == "accept":
        ParticipantLink.objects.filter(pk=participant_link_id, user=user).update(is_accepted=True)
    elif action == "reject":
        ParticipantLink.objects.filter(pk=participant_link_id, user=user).delete()
        # User rejected the invitation - revoke access to all historical meeting notifications
        from notifications.services import revoke_access_to_resource  # noqa: PLC0415
        revoke_access_to_resource(
            user_id=user.id,
            object_type=notification.related_object_type,
            object_id=notification.related_object_id
        )


def _handle_task_assignment(notification, action, user):
    """Accept → clear the invite-pending flag; reject → clear the owner or approver field and revoke access."""
    from task.models import Task  # noqa: PLC0415

    task_id = notification.related_object_id
    change_type = notification.metadata.get("change_type", "")

    try:
        task = Task.objects.get(pk=task_id)
    except (Task.DoesNotExist, ValueError):
        logger.warning("Task %s not found for assignment response", task_id)
        return

    if action == "accept":
        if change_type == "task_approver":
            task.approver_invite_pending = False
            task.save(update_fields=["approver_invite_pending"])
        else:
            task.owner_invite_pending = False
            task.save(update_fields=["owner_invite_pending"])
    elif action == "reject":
        if change_type == "task_approver":
            task.approver = None
            task.approver_invite_pending = False
        else:
            task.owner = None
            task.owner_invite_pending = False
        task.save()
        # User rejected the assignment - revoke access to all historical task notifications
        from notifications.services import revoke_access_to_resource  # noqa: PLC0415
        revoke_access_to_resource(
            user_id=user.id,
            object_type=notification.related_object_type,
            object_id=notification.related_object_id
        )


async def stream_notifications(request):
    """
    SSE endpoint: GET /api/notifications/stream/

    Opens a long-lived HTTP response that pushes real-time notification events
    to the authenticated browser tab using the text/event-stream protocol.

    Authentication
    --------------
    The native browser EventSource API cannot set custom headers, so the JWT
    is accepted in two ways (checked in order):

    1. ``Authorization: Bearer <token>`` header  (Axios / fetch callers)
    2. ``?token=<token>`` query parameter         (native EventSource)

    Returns HTTP 401 if no valid token is found.

    Reconnect / Last-Event-ID
    -------------------------
    The browser automatically sends a ``Last-Event-ID`` header on reconnect.
    It contains the UUID of the last received Notification, allowing the view
    to replay later database rows before resuming the live Redis Pub/Sub
    stream. The query param ``?lastEventId=`` serves as a fallback for manual
    reconnects and polyfill clients. ``?lastEventCreatedAt=`` preserves a
    timestamp fallback when the UUID cursor has already been deleted.

    Nginx integration
    -----------------
    ``X-Accel-Buffering: no`` disables Nginx response buffering so events are
    delivered immediately rather than batched.
    """
    # ── 1. Extract the raw JWT string ────────────────────────────────────
    token_string: str | None = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token_string = auth_header.split(" ", 1)[1].strip()
    elif request.GET.get("token"):
        token_string = request.GET["token"]

    if not token_string:
        return HttpResponse("Unauthorized", status=401, content_type="text/plain")

    # ── 2. Validate token and resolve user ───────────────────────────────
    try:
        from rest_framework_simplejwt.tokens import AccessToken  # noqa: PLC0415

        validated = await sync_to_async(AccessToken)(token_string)
        user_id = validated["user_id"]
        User = get_user_model()
        user = await sync_to_async(User.objects.get)(pk=user_id)
    except Exception:
        logger.warning("SSE: invalid or expired token", exc_info=True)
        return HttpResponse("Unauthorized", status=401, content_type="text/plain")

    # NOTE: the database connection is released inside sse_event_generator, not
    # here. Releasing it at this point does not work: TenantSchemaMiddleware
    # resets search_path in a finally block that runs as soon as this view
    # returns the response object, which re-opens the connection before a
    # single byte of the stream has been sent.

    # ── 3. Resolve Last-Event-ID ─────────────────────────────────────────
    # Browser sets the ``Last-Event-ID`` *header* automatically on reconnect;
    # the query param is a manual fallback for the initial EventSource URL.
    last_event_id = (
        request.headers.get("Last-Event-ID")
        or request.GET.get("lastEventId")
    )
    last_event_created_at = request.GET.get("lastEventCreatedAt")

    if last_event_id and not await sync_to_async(_allow_replay_attempt)(user.id):
        retry_after = max(
            1,
            int(
                getattr(
                    settings,
                    "NOTIFICATION_SSE_REPLAY_RATE_WINDOW_SECONDS",
                    30,
                )
            ),
        )
        response = HttpResponse(
            "Too Many Replay Requests",
            status=429,
            content_type="text/plain",
        )
        response["Retry-After"] = str(retry_after)
        return response

    # ── 4. Return streaming response ─────────────────────────────────────
    response = StreamingHttpResponse(
        sse_event_generator(user.id, last_event_id, last_event_created_at),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"   # Prevent Nginx from buffering SSE chunks
    response["Connection"] = "keep-alive"
    return response


stream_notifications.csrf_exempt = True  # bypass CSRF without wrapping the async function
