import json
import logging
import requests
from django.core.cache import cache
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone as django_timezone

from spreadsheet.providers import (
    SpreadsheetDataProvider,
)
from task.models import Task
from ..models import (
    AgentMessage, AgentWorkflowRun, ImportedCSVFile,
    AgentWorkflowDefinition, AgentStepExecution,
)
from .. import data_service
from core.services import file_parser
from ..agent_utils import json_input, serialize_agent_messages
from .analysis import AnalysisMixin, _preprocess_spreadsheet
from .common import _coerce_json
from .insights import InsightsMixin
from .miro import MIRO_LEGACY_BG_QUEUED_MESSAGE, MiroMixin

logger = logging.getLogger(__name__)


def _call_gemini_calendar_from_analysis(
    spreadsheet_data,
    analysis_result,
    user_id=None,
    success_criteria=None,
    user_context=None,
    agent_session=None,
):
    """Suggest calendar events from spreadsheet + analysis context."""
    from core.services.gemini_client import call_gemini_json
    from ..generation_registry import (
        build_calendar_from_analysis_user_prompt,
        calendar_from_analysis_system_prompt,
        validate_calendar_events_response,
    )

    column_summary, cleaned_data, _criteria_text = _preprocess_spreadsheet(
        spreadsheet_data, success_criteria
    )
    system_prompt = calendar_from_analysis_system_prompt()
    user_prompt = build_calendar_from_analysis_user_prompt(
        column_summary, cleaned_data, analysis_result
    )
    if agent_session is None:
        raw = call_gemini_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            timeout=120,
        )
    else:
        from ..llm_client import call_llm as _call_llm_unified

        result = _call_llm_unified(
            agent_session=agent_session,
            provider='gemini',
            model='gemini-2.5-flash-lite',
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            max_output_tokens=4096,
            response_mime_type='application/json',
            call_purpose='calendar_suggestion',
        )
        raw = json.loads(result['text'])
    logger.info("Calling Gemini for calendar events user_id=%s", user_id)
    return validate_calendar_events_response(raw)


def _serialize_project_members(project, excluded_users=None):
    """Return a minimal project member list for LLM follow-up disambiguation."""
    from core.models import ProjectMember

    excluded_user_ids = {
        user.id for user in (excluded_users or []) if getattr(user, 'id', None)
    }
    members = (
        ProjectMember.objects.filter(project=project, is_active=True)
        .exclude(user_id__in=excluded_user_ids)
        .select_related('user')
    )

    serialized = []
    for member in members:
        user = member.user
        display_name = user.get_full_name().strip() or user.username or user.email
        serialized.append(
            {
                'username': user.username,
                'email': user.email,
                'display_name': display_name,
            }
        )
    return serialized


def _normalize_llm_chat_output(output):
    """Normalize LLM follow-up output to {status, text, forwards}."""
    parsed = _coerce_json(output)
    if isinstance(parsed, dict):
        status = parsed.get('status') or 'completed'
        if status not in ('completed', 'needs_clarification'):
            status = 'completed'

        text = parsed.get('text')
        if not isinstance(text, str) or not text.strip():
            fallback_text = parsed.get('result') or parsed.get('output') or parsed.get('answer')
            if isinstance(fallback_text, str) and fallback_text.strip():
                text = fallback_text
            else:
                text = ''

        forwards = _coerce_json(parsed.get('forwards', []))
        if not isinstance(forwards, list):
            forwards = []

        normalized_forwards = []
        for item in forwards:
            if not isinstance(item, dict):
                continue
            username = item.get('username')
            content = item.get('content')
            if not isinstance(username, str) or not username.strip():
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            normalized_forwards.append(
                {
                    'username': username.strip(),
                    'content': content.strip(),
                }
            )

        if text.strip():
            return {
                'status': status,
                'text': text.strip(),
                'forwards': normalized_forwards,
            }

    if isinstance(parsed, str) and parsed.strip():
        return {
            'status': 'completed',
            'text': parsed.strip(),
            'forwards': [],
        }
    return None


_FOLLOWUP_SYSTEM_PROMPT = """\
You are the MediaJira post-analysis follow-up assistant.

Your job is limited to one follow-up after an analysis has already been completed.

You must:
1. Read the analysis result and the chat history.
2. Produce a clear user-facing reply in plain business language.
3. Optionally prepare structured forwards when the user explicitly asks to forward or notify project members.

You must not:
- create tasks
- invent project members
- guess ambiguous recipients

Important input rules:
- The chat history is a serialized transcript with role-based labels such as [user]: and [assistant]:.
- Do not expect usernames inside the transcript.
- current_username is the exact username of the current user when available.
- Treat the final [user]: turn as the latest follow-up request.
- If the final user request says "me", "myself", or "myself in chat", resolve the recipient to current_username.
- If forwarding is requested, identify recipients only from project_members.

Output rules:
- Return valid JSON only.
- Do not wrap the JSON in markdown fences.
- The JSON schema must be:
  {
    "status": "completed" | "needs_clarification",
    "text": "string",
    "forwards": [
      {
        "username": "exact project username only",
        "content": "string"
      }
    ]
  }
- "text" is always required.
- "forwards" must always be present and be an array.
- Use "completed" when the request has been fully handled.
- Use "needs_clarification" when forwarding was requested but the recipient is missing, ambiguous, or not uniquely identifiable from project_members.
- Only use exact usernames that exist in project_members.
- Only ask for clarification on "me" or "myself" if current_username is missing, empty, or not found in project_members.
- Never use first name or last name alone as a recipient identifier.
- If the user only wants explanation or summarization, return forwards as [].\
"""


def _call_gemini_chat(
    chat_messages,
    user_id=None,
    analysis_result=None,
    project_members=None,
    current_username='',
    agent_session=None,
):
    """Call Gemini for post-analysis follow-up. Replaces _call_dify_chat."""
    from core.services.gemini_client import call_gemini_json

    user_prompt = (
        f"Chat history:\n  {chat_messages}\n\n"
        f"Analysis result JSON:\n  {json_input(analysis_result) if analysis_result else '{}'}\n\n"
        f"Project members JSON:\n  {json_input(project_members or [])}\n\n"
        f"Current username:\n  {current_username or ''}\n\n"
        f"Return valid JSON only."
    )

    try:
        if agent_session is None:
            parsed = call_gemini_json(
                system_prompt=_FOLLOWUP_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.5,
                timeout=120,
            )
        else:
            from ..llm_client import call_llm as _call_llm_unified

            result = _call_llm_unified(
                agent_session=agent_session,
                provider='gemini',
                model='gemini-2.5-flash-lite',
                system_prompt=_FOLLOWUP_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.5,
                max_output_tokens=4096,
                response_mime_type='application/json',
                call_purpose='follow_up_chat',
            )
            parsed = json.loads(result['text'])
    except Exception as e:
        logger.error("Gemini chat call failed: %s", e)
        raise RuntimeError(f"Gemini chat failed: {e}") from e

    normalized = _normalize_llm_chat_output(parsed)
    if normalized:
        return normalized

    raise RuntimeError("Gemini chat returned unexpected output format")


class AgentOrchestrator(AnalysisMixin, InsightsMixin, MiroMixin):
    def __init__(self, user, project, session):
        self.user = user
        self.project = project
        self.session = session
        self.spreadsheet_provider = SpreadsheetDataProvider(user)

    def resolve_external_approval_stream(
        self,
        approval_id,
        decision,
        draft=None,
        destination=None,
    ):
        """Complete or reject a pending external commit; resume workflow when applicable."""
        from django.utils import timezone as tz

        from ..approval_gate import resolve_pending
        from ..models import AgentPendingExternalApproval
        from miro.models import Board

        try:
            result = resolve_pending(
                orchestrator=self,
                pending_id=str(approval_id),
                decision=decision,
                draft=draft or {},
                destination=destination,
            )
        except ValueError as e:
            yield {'type': 'error', 'content': str(e)}
            return

        for ev in result.sse_events:
            yield ev

        try:
            pending = AgentPendingExternalApproval.objects.get(
                id=approval_id, session=self.session, is_deleted=False,
            )
        except AgentPendingExternalApproval.DoesNotExist:
            return

        wr = pending.workflow_run
        ex = pending.step_execution

        if decision == 'reject':
            if wr:
                wr.status = 'failed'
                wr.error_message = 'External action rejected by user.'
                wr.save(update_fields=['status', 'error_message', 'updated_at'])
            if ex:
                ex.status = 'failed'
                ex.error_message = 'Rejected by user'
                ex.completed_at = tz.now()
                ex.save(update_fields=['status', 'error_message', 'completed_at', 'updated_at'])
            return

        if ex and wr and wr.workflow_definition_id:
            ex.status = 'completed'
            ex.output_data = result.output_data
            ex.completed_at = tz.now()
            ex.save(update_fields=['status', 'output_data', 'completed_at', 'updated_at'])

            wf_patch = result.workflow_run_patch or {}
            uf = ['status', 'current_step_order', 'updated_at', 'error_message']
            wr.error_message = None
            if 'created_tasks' in wf_patch:
                wr.created_tasks = wf_patch['created_tasks']
                uf.append('created_tasks')
            if 'created_decisions' in wf_patch:
                wr.created_decisions = wf_patch['created_decisions']
                uf.append('created_decisions')
            if wf_patch.get('miro_board_id'):
                wr.miro_board = Board.objects.get(id=wf_patch['miro_board_id'])
                uf.append('miro_board')
            if wf_patch.get('miro_snapshot') is not None:
                wr.miro_snapshot = wf_patch['miro_snapshot']
                uf.append('miro_snapshot')

            wr.status = 'analyzing'
            wr.current_step_order = ex.step_order + 1
            wr.save(update_fields=uf)

            yield from self._execute_steps(wr, result.output_data or {})

    def handle_message(self, message, spreadsheet_id=None, sheet_id=None, csv_filename=None,
                       action=None, file_id=None, calendar_context=None,
                       draft_context=None,
                       workflow_id=None, column_mapping=None,
                       approval_id=None, approval_decision=None,
                       approval_draft=None, generation_outputs=None, user_context=None,
                       reviewed_anomalies=None):
        """Main entry point. Routes calendar context first, then workflow engine or legacy logic.

        Yields SSE chunks as dicts.
        """
        if action == 'analyze_spreadsheet_insights':
            yield from self.analyze_spreadsheet_insights(
                spreadsheet_id, sheet_id=sheet_id,
            )
            yield {"type": "done"}
            return

        if action == 'confirm_anomalies':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()
            yield from self.confirm_anomalies(latest_run, reviewed_anomalies)
            yield {"type": "done"}
            return

        if action == 'resolve_external_approval':
            if not approval_id or not approval_decision:
                yield {'type': 'error', 'content': 'approval_id and approval_decision are required.'}
                return
            yield from self.resolve_external_approval_stream(
                approval_id,
                approval_decision,
                draft=approval_draft,
                destination=None,
            )
            yield {'type': 'done'}
            return

        # --- Calendar context takes priority over all other routing ---
        if calendar_context:
            yield from self.answer_calendar_question(message, calendar_context)
            yield {"type": "done"}
            return

        # --- Draft context: answer using the draft's real content (read-only) ---
        if draft_context:
            yield from self.answer_draft_question(message, draft_context)
            yield {"type": "done"}
            return

        if action == 'create_decisions':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()

            if latest_run and self._workflow_run_analysis(latest_run).get(
                'recommended_decision_tree', {}
            ).get('nodes'):
                yield from self.create_decisions_from_analysis(latest_run)
                yield {'type': 'done'}
                return
            if latest_run and latest_run.workflow_definition:
                yield from self._resume_workflow(latest_run)
                yield {'type': 'done'}
                return
            yield {'type': 'error', 'content': 'No analysis found to create decisions from.'}
            yield {'type': 'done'}
            return

        # --- Resume a paused workflow ---
        if action == 'create_tasks':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()

            if latest_run and self._workflow_run_analysis(latest_run).get('recommended_tasks'):
                # Commit tasks from the stored analysis instead of resuming the
                # workflow, which may pause again on legacy await_confirmation steps.
                yield from self.create_tasks_from_analysis(latest_run)
                yield {"type": "done"}
                return
            if latest_run and latest_run.workflow_definition:
                yield from self._resume_workflow(latest_run)
                yield {"type": "done"}
                return
            yield from self._legacy_confirm(action, latest_run)
            yield {"type": "done"}
            return

        # Resume a workflow paused at await_confirmation (user clicked Continue in chat).
        if action == 'resume_workflow':
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                is_deleted=False,
            ).order_by('-created_at').first()
            if latest_run and latest_run.workflow_definition:
                yield from self._resume_workflow(latest_run)
            else:
                yield {
                    'type': 'text',
                    'content': (
                        'This workflow has already finished or was continued. '
                        'Start a new message to run it again.'
                    ),
                }
            yield {'type': 'done'}
            return

        # Resume after user confirms / edits the detected column mapping.
        if action == 'confirm_columns':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()

            if latest_run and latest_run.workflow_definition:
                # Inject the user-approved mapping so NormalizeDataExecutor
                # can pick it up from input_data.
                extra = {'column_mapping': column_mapping} if column_mapping else {}
                yield from self._resume_workflow(latest_run, extra_input=extra)
            else:
                yield {"type": "error", "content": "No paused workflow to confirm."}
            yield {"type": "done"}
            return

        if action == 'generate_miro':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()
            yield from self._legacy_confirm(action, latest_run)
            yield {"type": "done"}
            return

        if action == 'start_follow_up':
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                analysis_result__isnull=False,
                is_deleted=False,
            ).order_by('-created_at').first()
            yield from self._start_follow_up(latest_run)
            yield {"type": "done"}
            return

        if action == 'cancel_follow_up':
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                analysis_result__isnull=False,
                is_deleted=False,
            ).order_by('-created_at').first()
            yield from self._cancel_follow_up(latest_run)
            yield {"type": "done"}
            return

        # --- Start a new workflow (file upload / analyze action / explicit workflow_id) ---
        if file_id or spreadsheet_id or csv_filename or (action == 'analyze') or workflow_id:
            workflow_def = self._resolve_workflow(
                workflow_id=workflow_id,
                action=action,
                file_id=file_id,
                user_message=message
            )
            if workflow_def:
                yield from self._start_workflow(
                    workflow_def,
                    file_id=file_id,
                    spreadsheet_id=spreadsheet_id,
                    csv_filename=csv_filename,
                    generation_outputs=generation_outputs,
                    user_context=user_context,
                )
                yield {"type": "done"}
                return

        # --- No workflow match → full legacy logic (includes follow-up chat) ---
        yield from self._legacy_handle(
            message, spreadsheet_id, csv_filename, action, file_id
        )

    def _fetch_events_for_context(self, calendar_context):
        """Fetch calendar events for the given context.

        For a specific event: returns just that event.
        For a calendar view: returns events within the currently visible date
        range (day / week / month), so the AI only discusses what the user sees.
        Falls back to a ±7-day window when no view info is available.
        """
        try:
            from calendars.models import Event
        except ImportError:
            return []

        org_id = getattr(self.user, 'organization_id', None)
        if not org_id:
            return []

        event_id = calendar_context.get('eventId')

        # Specific event — return it regardless of time
        if event_id:
            try:
                return [Event.objects.select_related('calendar').get(
                    id=event_id, organization_id=org_id
                )]
            except Event.DoesNotExist:
                return []

        # Determine window from the calendar view the user is currently on
        import pytz as _pytz
        from datetime import datetime as _dt, timedelta as _td, time as _time

        current_date_str = calendar_context.get('currentDate')
        current_view = (calendar_context.get('currentView') or 'week').lower()
        user_tz_name = (calendar_context.get('userTimezone') or 'UTC').strip()
        try:
            user_tz = _pytz.timezone(user_tz_name)
        except _pytz.UnknownTimeZoneError:
            user_tz = _pytz.utc

        if current_date_str:
            try:
                base = _dt.strptime(current_date_str, '%Y-%m-%d').date()
                if current_view == 'day':
                    view_start = base
                    view_end = base
                elif current_view == 'month':
                    import calendar as _cal
                    view_start = base.replace(day=1)
                    view_end = base.replace(day=_cal.monthrange(base.year, base.month)[1])
                else:  # week (default)
                    # Monday of the week containing base; extend 2 extra weeks so
                    # follow-up questions like "what about next week?" have data.
                    monday = base - _td(days=base.weekday())
                    view_start = monday
                    view_end = monday + _td(days=20)

                window_start = user_tz.localize(_dt.combine(view_start, _time.min)).astimezone(_pytz.utc)
                window_end = user_tz.localize(_dt.combine(view_end, _time.max)).astimezone(_pytz.utc)
            except (ValueError, Exception):
                now = django_timezone.now()
                window_start = now - django_timezone.timedelta(days=7)
                window_end = now + django_timezone.timedelta(days=7)
        else:
            now = django_timezone.now()
            window_start = now - django_timezone.timedelta(days=7)
            window_end = now + django_timezone.timedelta(days=7)

        qs = Event.objects.filter(
            organization_id=org_id,
            start_datetime__gte=window_start,
            start_datetime__lte=window_end,
            is_deleted=False,
        ).select_related('calendar').order_by('start_datetime')

        # Filter by visible calendar IDs if provided in context
        calendar_ids = calendar_context.get('calendarIds') or []
        calendar_id = calendar_context.get('calendarId')
        if calendar_ids:
            qs = qs.filter(calendar__id__in=calendar_ids)
        elif calendar_id:
            qs = qs.filter(calendar__id=calendar_id)

        return list(qs[:30])

    def _create_calendar_event(self, org_id, event_spec, user_tz=None):
        """Create a single calendar event from a dict spec. Returns event id or None."""
        try:
            from calendars.models import Calendar as CalendarModel, Event as EventModel
            from dateutil import parser as date_parser
            import pytz

            def _parse_dt(dt_str):
                if not dt_str:
                    return None
                # Dify may echo back the timezone-name suffix we used for existing
                # events (e.g. "2026-03-31T14:00:00 Australia/Melbourne").
                # dateutil cannot parse IANA timezone names inline, so strip the
                # suffix and let user_tz.localize() apply the correct timezone.
                raw = str(dt_str).strip()
                date_part = raw.split(" ")[0] if " " in raw else raw
                dt = date_parser.parse(date_part)
                if dt.tzinfo is None and user_tz:
                    dt = user_tz.localize(dt)
                elif dt.tzinfo is None:
                    dt = pytz.utc.localize(dt)
                return dt

            # Prefer the user's primary calendar; fall back to any calendar they own
            cal = (
                CalendarModel.objects.filter(
                    organization_id=org_id,
                    owner=self.user,
                    is_deleted=False,
                ).order_by('-is_primary').first()
            )
            if not cal:
                return None
            tz_name = str(user_tz) if user_tz else "UTC"
            new_event = EventModel.objects.create(
                organization_id=org_id,
                calendar=cal,
                created_by=self.user,
                title=event_spec.get("title", "New Event"),
                description=event_spec.get("description", ""),
                start_datetime=_parse_dt(event_spec.get("start_datetime")),
                end_datetime=_parse_dt(event_spec.get("end_datetime")),
                timezone=tz_name,
            )
            return str(new_event.id)
        except Exception as e:
            logger.error(f"Failed to create calendar event: {e}")
            return None

    # Cap on the draft text inlined into the LLM context (chars).
    _DRAFT_PAYLOAD_MAX_CHARS = 12000

    def answer_draft_question(self, message, draft_context):
        """Answer a question using a draft's real content (AGENT-7, read-only).

        The Agent reuses notion_editor's existing logic in-process — never
        reimplementing it:
          * permissions: DraftViewSet.get_queryset() is the single source of
            truth for which drafts this user may see, and
          * rendering: notion_editor's _html_to_plain_text extracts block text.
        notion_editor's source files are unchanged, and the Agent holds no
        draft/block/permission logic of its own.

        TODO(AGENT-7 write direction): if Agent → Draft (create/update) becomes
        in scope, drive it through notion_editor's existing DraftViewSet
        create/update flow (no duplicate draft/block logic) and surface a
        confirmation card linking back into the Notion module.
        """
        from types import SimpleNamespace
        from notion_editor.views import DraftViewSet
        from notion_editor.services import _html_to_plain_text
        from core.services.gemini_client import call_gemini, _get_api_key as _gemini_key

        draft_ref = None
        if isinstance(draft_context, dict):
            draft_ref = draft_context.get('draftId') or draft_context.get('draft_id')
        if not draft_ref:
            yield {"type": "error", "content": "No draft was specified."}
            return

        # Permissions come entirely from DraftViewSet.get_queryset(), which
        # only reads request.user — so a minimal stub request is enough. The
        # Agent never reimplements the user/is_deleted access filter.
        view = DraftViewSet()
        view.request = SimpleNamespace(user=self.user)
        accessible = view.get_queryset()

        # Identify the draft within the already permission-scoped queryset
        # (slug is the public identifier; fall back to a legacy numeric pk).
        draft_ref = str(draft_ref)
        draft = accessible.filter(slug=draft_ref).first()
        if draft is None and draft_ref.isdigit():
            draft = accessible.filter(pk=int(draft_ref)).first()
        if draft is None:
            yield {
                "type": "error",
                "content": "That draft could not be found or you do not have access to it.",
            }
            return

        if not _gemini_key():
            yield {"type": "error", "content": "Agent AI is not configured. Please set GEMINI_API_KEY."}
            return

        # Render blocks to text by reusing notion_editor's HTML extractor; the
        # Agent does not parse or re-model blocks itself.
        parts = []
        title = (draft.title or '').strip()
        if title:
            parts.append(f"# {title}")
        blocks = draft.content_blocks if isinstance(draft.content_blocks, list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            content = block.get('content') or {}
            html = ''
            if isinstance(content, dict):
                html = content.get('html') or content.get('text') or ''
            text = _html_to_plain_text(html).strip()
            if text:
                parts.append(text)
        draft_text = "\n\n".join(parts)
        if len(draft_text) > self._DRAFT_PAYLOAD_MAX_CHARS:
            draft_text = draft_text[:self._DRAFT_PAYLOAD_MAX_CHARS].rstrip() + "\n\n[... draft truncated for length ...]"

        system_prompt = (
            "You are a helpful writing assistant embedded in a Notion-style draft editor. "
            "You help the user understand, summarize, expand, and answer questions about THIS draft. "
            "Use only the provided draft content as ground truth; if the answer is not in the draft, "
            "say so plainly. Respond in clear plain text (no JSON, no markdown code fences)."
        )
        user_prompt = (
            f"Draft title: {draft.title}\n\n"
            f'Draft content:\n"""\n{draft_text}\n"""\n\n'
            f"User question: {message}"
        )

        try:
            answer = call_gemini(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                timeout=90,
            )
        except Exception as e:
            logger.error(f"Gemini draft Q&A error: {e}")
            yield {"type": "error", "content": "Failed to get AI response. Please try again."}
            return

        answer_text = (answer or "").strip() or "I couldn't generate a response for that draft."
        yield {"type": "text", "content": answer_text}

    def answer_calendar_question(self, message, calendar_context):
        """Answer calendar-related questions using real event data via Dify AI."""
        yield {"type": "text", "content": "Looking up your calendar data..."}

        events = self._fetch_events_for_context(calendar_context)

        # Resolve user timezone from context (fallback to UTC)
        import pytz
        user_tz_name = (calendar_context.get('userTimezone') or 'UTC').strip()
        try:
            user_tz = pytz.timezone(user_tz_name)
        except pytz.UnknownTimeZoneError:
            user_tz = pytz.utc
            user_tz_name = 'UTC'

        # Serialize events for Dify using user's local timezone
        now = django_timezone.now()
        now_local = now.astimezone(user_tz)
        events_data = []
        for evt in events:
            is_past = evt.start_datetime < now
            local_start = evt.start_datetime.astimezone(user_tz)
            local_end = evt.end_datetime.astimezone(user_tz)
            events_data.append({
                "id": str(evt.id),
                "title": evt.title or "(No title)",
                "start_datetime": local_start.strftime(f'%Y-%m-%dT%H:%M:%S {user_tz_name}'),
                "end_datetime": local_end.strftime(f'%Y-%m-%dT%H:%M:%S {user_tz_name}'),
                "is_past": is_past,
                "calendar": evt.calendar.name,
                "location": evt.location or "",
                "description": evt.description or "",
            })

        calendar_payload = {
            "current_time_local": now_local.strftime(f'%Y-%m-%dT%H:%M:%S {user_tz_name}'),
            "user_timezone": user_tz_name,
            "events": events_data,
        }
        calendar_data_str = json.dumps(calendar_payload, ensure_ascii=False)

        # Call Gemini Calendar Assistant
        from core.services.gemini_client import call_gemini, _get_api_key as _gemini_key
        if not _gemini_key():
            yield {"type": "error", "content": "Calendar AI is not configured. Please set GEMINI_API_KEY."}
            return

        _calendar_system_prompt = (
            "You are a helpful calendar assistant. You answer questions about the user's upcoming events "
            "and help them create new calendar events when asked.\n\n"
            "You will receive calendar_data (JSON with current_time_local, user_timezone, and events list) "
            "and the user's question.\n\n"
            "Return ONLY valid JSON (no markdown, no explanation) with this structure:\n"
            "{\n"
            '  "answer": "your plain-language response to the user",\n'
            '  "create_events": [\n'
            "    {\n"
            '      "title": "event title",\n'
            '      "start_datetime": "YYYY-MM-DDTHH:MM:SS",\n'
            '      "end_datetime": "YYYY-MM-DDTHH:MM:SS",\n'
            '      "location": "optional location",\n'
            '      "description": "optional description"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Always include 'answer'.\n"
            "- Only include 'create_events' entries when the user explicitly asks to create or schedule an event.\n"
            "- If no events to create, return create_events as [].\n"
            "- Datetimes must be in the user's timezone as shown in calendar_data."
        )

        try:
            raw_answer = call_gemini(
                system_prompt=_calendar_system_prompt,
                user_prompt=(
                    f"Calendar data:\n{calendar_data_str}\n\n"
                    f"User question: {message}\n\n"
                    f"Return JSON only."
                ),
                temperature=0.3,
                timeout=90,
            )
        except Exception as e:
            logger.error(f"Gemini calendar workflow error: {e}")
            yield {"type": "error", "content": "Failed to get AI response. Please try again."}
            return

        # Parse AI response (expects JSON with answer + create_events array)
        text = raw_answer.strip()
        for fence in ('```json', '```'):
            if text.startswith(fence):
                text = text[len(fence):]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

        try:
            parsed = json.loads(text)
            answer_text = parsed.get("answer", raw_answer)
            # Prefer create_events (array); only fall back to create_event (single) when
            # the array is absent/empty — avoids duplicates if Dify returns both keys.
            events_to_create = parsed.get("create_events") or []
            if not events_to_create:
                single = parsed.get("create_event")
                if single and isinstance(single, dict):
                    events_to_create = [single]
            # Track whether Dify included ANY creation-related key (even if empty/declined).
            # Used to suppress the calendar invite when the user already asked to create.
            # Only True when Dify actually provided event data to create.
            # Key presence alone (e.g. create_events: null / []) does not count.
            had_creation_intent = bool(parsed.get("create_events")) or bool(parsed.get("create_event"))
        except (json.JSONDecodeError, AttributeError):
            answer_text = raw_answer
            events_to_create = []
            had_creation_intent = False

        org_id = getattr(self.user, 'organization_id', None)
        created_count = 0
        failed_count = 0
        calendar_refresh_emitted = False
        if events_to_create and org_id:
            from ..approval_gate import KIND_CALENDAR_EVENT, request_external_commit

            draft_events = [e for e in events_to_create if isinstance(e, dict)]
            gate = request_external_commit(
                orchestrator=self,
                workflow_run=None,
                step_execution=None,
                kind=KIND_CALENDAR_EVENT,
                draft={'events': draft_events},
                commit_context={
                    'organization_id': str(org_id),
                    'user_timezone': user_tz_name,
                },
            )
            for ev in gate.sse_events:
                yield ev
                if ev.get('type') == 'calendar_updated':
                    calendar_refresh_emitted = True
            if gate.paused:
                answer_text += (
                    '\n\n⏸ Event creation is waiting for your approval '
                    'in the approval panel.'
                )
            else:
                wf = gate.workflow_run_patch or {}
                created_ids = wf.get('created_event_ids') or []
                created_count = len(created_ids)
                failed_count = max(0, len(draft_events) - created_count)
                if created_count:
                    answer_text += (
                        f"\n\n✅ {created_count} calendar event"
                        f"{'s' if created_count != 1 else ''} created successfully."
                    )
                if failed_count:
                    answer_text += (
                        f"\n\n⚠️ {failed_count} event"
                        f"{'s' if failed_count != 1 else ''} could not be created automatically."
                    )

        yield {
            "type": "text",
            "content": answer_text,
        }
        if created_count and not calendar_refresh_emitted:
            yield {"type": "calendar_updated"}
        elif not had_creation_intent:
            # Only invite when the user asked a general calendar question,
            # not when they explicitly requested creation (even if Dify declined).
            yield {
                "type": "calendar_invite",
                "content": "Do you need me to create an event for you? If so, please tell me the specific time (down to the hour).",
            }

    def _start_follow_up(self, workflow_run):
        if not workflow_run or not workflow_run.analysis_result:
            yield {"type": "error", "content": "No analysis found to start a follow-up chat."}
            return

        if workflow_run.chat_followed_up:
            yield {"type": "error", "content": "Follow-up chat is already completed for this analysis."}
            return

        if not workflow_run.chat_follow_up_started:
            workflow_run.chat_follow_up_started = True
            workflow_run.save(update_fields=['chat_follow_up_started'])

        yield {
            "type": "follow_up_prompt",
            "content": "Follow-up chat started. Ask one follow-up question about the analysis, or include the exact username/email if you want me to prepare a forwarded message.",
            "data": {"workflow_run_id": str(workflow_run.id)},
        }

    def _cancel_follow_up(self, workflow_run):
        if not workflow_run or not workflow_run.analysis_result:
            yield {"type": "error", "content": "No analysis found to cancel a follow-up chat for."}
            return

        if workflow_run.chat_followed_up:
            yield {"type": "error", "content": "Follow-up chat is already completed for this analysis."}
            return

        if not workflow_run.chat_follow_up_started:
            yield {"type": "text", "content": "Follow-up chat is already inactive."}
            return

        workflow_run.chat_follow_up_started = False
        workflow_run.save(update_fields=['chat_follow_up_started'])
        yield {
            "type": "text",
            "content": "Follow-up chat closed.",
            "data": {"workflow_run_id": str(workflow_run.id)},
        }

    def _workflow_run_analysis(self, workflow_run):
        """Return analysis payload for a run, including completed step output."""
        analysis = workflow_run.analysis_result
        if isinstance(analysis, dict) and analysis.get('recommended_tasks'):
            return analysis

        last_execution = workflow_run.step_executions.filter(
            status='completed'
        ).order_by('-step_order').first()
        output_data = getattr(last_execution, 'output_data', None) or {}
        if isinstance(output_data, dict):
            step_analysis = output_data.get('analysis_result')
            if isinstance(step_analysis, dict) and step_analysis.get('recommended_tasks'):
                workflow_run.analysis_result = step_analysis
                workflow_run.save(update_fields=['analysis_result', 'updated_at'])
                return step_analysis

        return analysis if isinstance(analysis, dict) else {}

    def confirm_anomalies(self, workflow_run, reviewed_anomalies):
        """Persist the user's reviewed anomaly list and unlock task creation.

        Guard order is deliberate: the already-confirmed no-op runs BEFORE any
        payload validation so a reload/retry that sends a partial or stale
        payload no-ops cleanly instead of raising a validation error.
        """
        # 1. Missing run/analysis.
        analysis = self._workflow_run_analysis(workflow_run) if workflow_run else {}
        if not workflow_run or not isinstance(analysis, dict) or not analysis:
            yield {"type": "error", "content": "No analysis to confirm."}
            return

        # 2. Already confirmed -> safe idempotent no-op (before validation).
        if analysis.get('anomalies_confirmed'):
            yield {
                "type": "anomalies_confirmed",
                "content": "Anomalies already confirmed.",
                "data": analysis,
                "already_confirmed": True,
            }
            return

        stored = analysis.get('anomalies') or []
        existing_by_id = {a.get('id'): a for a in stored if isinstance(a, dict)}

        payload = reviewed_anomalies if isinstance(reviewed_anomalies, list) else []
        payload_ids = [
            entry.get('id') for entry in payload if isinstance(entry, dict)
        ]

        # 3. Completeness check (atomic): payload ids must exactly equal the
        #    stored anomaly id set -- reject on unknown / missing / duplicate.
        stored_ids = set(existing_by_id.keys())
        payload_id_set = set(payload_ids)
        unknown = sorted(i for i in payload_id_set if i not in stored_ids)
        missing = sorted(i for i in stored_ids if i not in payload_id_set)
        duplicate = sorted({i for i in payload_ids if payload_ids.count(i) > 1})
        if unknown or missing or duplicate:
            yield {
                "type": "error",
                "content": (
                    f"Anomaly review incomplete: unknown={unknown}, "
                    f"missing={missing}, duplicate={duplicate}"
                ),
            }
            return

        # 4. Validate + merge each entry onto the full stored anomaly object.
        valid_severities = {'critical', 'warning', 'info'}
        merged = []
        for entry in payload:
            anomaly_id = entry.get('id')
            base = dict(existing_by_id[anomaly_id])
            severity = entry.get('severity')
            if severity is not None:
                if severity not in valid_severities:
                    yield {
                        "type": "error",
                        "content": f"Invalid severity '{severity}' for {anomaly_id}.",
                    }
                    return
                base['severity'] = severity
            description = entry.get('description')
            if description is not None:
                if not isinstance(description, str):
                    yield {
                        "type": "error",
                        "content": f"Invalid description for {anomaly_id}.",
                    }
                    return
                base['description'] = description.strip()[:1000]
            base['included'] = bool(entry.get('included', True))
            merged.append(base)

        # 5. Persist reviewed list + confirmation flag (keep original anomalies).
        analysis['reviewed_anomalies'] = merged
        analysis['anomalies_confirmed'] = True
        workflow_run.analysis_result = analysis
        workflow_run.save(update_fields=['analysis_result', 'updated_at'])

        # Update the stored analysis message so a reload restores the same card
        # in its locked, reviewed state (rather than an editable duplicate).
        analysis_message = (
            AgentMessage.objects
            .filter(session=self.session, role='assistant', metadata__has_key='anomalies')
            .order_by('-created_at')
            .first()
        )
        if analysis_message:
            meta = analysis_message.metadata or {}
            meta['anomalies_confirmed'] = True
            meta['reviewed_anomalies'] = merged
            analysis_message.metadata = meta
            analysis_message.save(update_fields=['metadata'])

        included_count = sum(1 for a in merged if a.get('included'))
        # 6. Emit confirmation with full merged objects so the UI can re-render.
        yield {
            "type": "anomalies_confirmed",
            "content": f"Anomalies confirmed ({included_count} included).",
            "data": analysis,
        }

    def create_decisions_from_analysis(self, workflow_run):
        """Create Decision tree directly from analysis results."""
        yield {'type': 'text', 'content': 'Creating decisions...'}

        existing_decision_ids = getattr(workflow_run, 'created_decisions', []) or []
        if existing_decision_ids:
            yield {
                'type': 'decision_draft',
                'content': f'Decisions already created ({len(existing_decision_ids)}).',
                'data': {
                    'decision_ids': existing_decision_ids,
                },
            }
            return

        analysis = self._workflow_run_analysis(workflow_run)
        tree = (analysis or {}).get('recommended_decision_tree') or {}
        nodes = tree.get('nodes') or []
        if not nodes:
            yield {'type': 'text', 'content': 'No decision nodes found in analysis.'}
            return

        from ..approval_gate import KIND_DECISION_TREE, request_external_commit

        draft = {'recommended_decision_tree': tree}
        commit_context = {
            'input_data': {'analysis_result': analysis},
            'analysis_result': analysis,
        }
        gate = request_external_commit(
            orchestrator=self,
            workflow_run=workflow_run,
            step_execution=None,
            kind=KIND_DECISION_TREE,
            draft=draft,
            commit_context=commit_context,
        )
        for ev in gate.sse_events:
            yield ev
        if gate.paused:
            return

        decision_ids = (gate.workflow_run_patch or {}).get('created_decisions') or []
        workflow_run.created_decisions = decision_ids
        workflow_run.save(update_fields=['created_decisions'])

    def create_tasks_from_analysis(self, workflow_run):
        """Create Tasks directly from analysis recommended_tasks.

        Recommended tasks are independent of anomaly review state; explicit
        create_tasks always commits from ``recommended_tasks`` when present.
        """
        yield {"type": "text", "content": "Creating tasks..."}

        existing_task_ids = getattr(workflow_run, "created_tasks", []) or []
        if existing_task_ids:
            decision = workflow_run.decision
            yield {
                "type": "task_created",
                "content": f"Tasks already created ({len(existing_task_ids)}).",
                "data": {
                    "task_ids": existing_task_ids,
                    "decision_id": decision.id if decision else None,
                },
            }
            return

        analysis = self._workflow_run_analysis(workflow_run)

        # Anomaly confirmation gate: when the analysis surfaced anomalies, they
        # must be reviewed + confirmed before tasks are created, so data-quality
        # issues do not silently propagate downstream. The lightweight in-sheet
        # "spreadsheet insights" path auto-confirms (it sets anomalies_confirmed
        # and _source='spreadsheet_insights'), so it is not blocked here.
        had_anomalies = bool(analysis.get('anomalies'))
        is_insights_flow = analysis.get('_source') == 'spreadsheet_insights'
        if (
            had_anomalies
            and not is_insights_flow
            and not analysis.get('anomalies_confirmed')
        ):
            yield {
                "type": "error",
                "content": "Anomalies must be confirmed before creating tasks.",
            }
            return

        recommended_tasks = analysis.get("recommended_tasks", [])
        if not recommended_tasks:
            yield {"type": "error", "content": "No recommended tasks found in analysis."}
            return

        reviewed = analysis.get('reviewed_anomalies') or []
        included_anomalies = [a for a in reviewed if a.get('included', True)]

        decision = workflow_run.decision
        from ..approval_gate import KIND_TASK, request_external_commit

        draft = {'recommended_tasks': recommended_tasks}
        commit_context = {
            'input_data': {'analysis_result': analysis},
            'analysis_result': analysis,
            'decision_id': decision.id if decision else None,
            'included_anomalies': included_anomalies,
            'reviewed_anomalies': reviewed,
        }
        gate = request_external_commit(
            orchestrator=self,
            workflow_run=workflow_run,
            step_execution=None,
            kind=KIND_TASK,
            draft=draft,
            commit_context=commit_context,
        )
        for ev in gate.sse_events:
            yield ev
        if gate.paused:
            return

        task_ids = (gate.workflow_run_patch or {}).get('created_tasks') or []
        workflow_run.created_tasks = task_ids
        workflow_run.save(update_fields=['created_tasks'])

        workflow_run.status = 'completed'
        workflow_run.save(update_fields=['status'])

    # ------------------------------------------------------------------
    # Workflow engine methods (AGENT-9)
    # ------------------------------------------------------------------

    def _resolve_workflow(
        self,
        workflow_id=None,
        action=None,
        file_id=None,
        spreadsheet_id=None,
        csv_filename=None,
        user_message='',
    ):
        """
        Resolve which workflow definition to run.

        - Explicit workflow_id: user-selected template/workflow (highest priority).
        - File upload / analyze / spreadsheet paths: system default workflow (legacy bot).
        - Plain text without workflow_id: no workflow (handled by legacy chat).
        """
        if workflow_id:
            try:
                return AgentWorkflowDefinition.objects.get(
                    id=workflow_id, status='active', is_deleted=False,
                )
            except AgentWorkflowDefinition.DoesNotExist:
                return None

        if file_id or action == 'analyze' or spreadsheet_id or csv_filename:
            return self._get_system_default_workflow()

        return None

    def _get_system_default_workflow(self):
        """Get system default workflow."""
        return AgentWorkflowDefinition.objects.filter(
            project__isnull=True, is_system=True, is_default=True,
            status='active', is_deleted=False,
        ).first()

    def _prepare_input_data(self, file_id=None, spreadsheet_id=None, csv_filename=None):
        """Build the initial input_data dict for the workflow engine.

        file_id is included in the returned dict so NormalizeDataExecutor can
        persist confirmed column mappings and row data to ImportedDataField /
        ImportedDataRecord without needing a separate DB lookup.
        """
        import os as _os

        if file_id:
            record = ImportedCSVFile.objects.get(
                id=file_id, project=self.project, is_deleted=False,
            )
            csv_dir = data_service._get_csv_dir()
            filepath = _os.path.join(csv_dir, _os.path.basename(record.filename))
            return {
                'spreadsheet_data': file_parser.parse_file_to_json(filepath, record.filename),
                'file_id': str(file_id),
            }

        if spreadsheet_id:
            payload = self.spreadsheet_provider.get_analysis_payload(spreadsheet_id)
            return {
                'spreadsheet_data': payload,
                'spreadsheet_id': payload['id'],
            }

        if csv_filename:
            record = ImportedCSVFile.objects.get(
                filename=csv_filename, project=self.project, is_deleted=False,
            )
            csv_dir = data_service._get_csv_dir()
            filepath = _os.path.join(csv_dir, _os.path.basename(record.filename))
            columns, rows = data_service._read_csv_file(filepath)
            return {
                'spreadsheet_data': {
                    'name': record.original_filename,
                    'sheets': [{'name': 'Sheet1', 'columns': columns, 'rows': rows}],
                },
                'file_id': str(record.id),
            }

        return {}

    def _start_workflow(self, workflow_def, file_id=None, spreadsheet_id=None,
                        csv_filename=None, generation_outputs=None, user_context=None):
        """Create a new WorkflowRun and execute steps."""
        from ..generation_registry import normalize_generation_outputs

        outputs = normalize_generation_outputs(generation_outputs)
        input_data = self._prepare_input_data(
            file_id=file_id,
            spreadsheet_id=spreadsheet_id,
            csv_filename=csv_filename,
        )
        input_data['generation_outputs'] = outputs

        # _prepare_input_data's spreadsheet branch already access-checked and
        # returns the id; otherwise (e.g. file-upload path) re-check the raw id.
        resolved_spreadsheet_id = input_data.get('spreadsheet_id')
        if resolved_spreadsheet_id is None and spreadsheet_id:
            resolved_spreadsheet_id = self.spreadsheet_provider.accessible_spreadsheet_id(
                spreadsheet_id
            )

        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=workflow_def,
            status='analyzing',
            current_step_order=1,
            spreadsheet_id=resolved_spreadsheet_id,
            generation_outputs_requested=outputs,
        )
        if user_context:
            cache.set(f"agent:context:{workflow_run.id}", user_context, 3600)

        yield from self._execute_steps(workflow_run, input_data)

    def _emit_calendar_events_if_requested(self, workflow_run, input_data):
        """After workflow steps, optionally call Gemini for calendar_events."""
        from ..generation_registry import (
            GenerationValidationError,
            normalize_generation_outputs,
        )

        outputs = input_data.get('generation_outputs')
        if outputs is None:
            outputs = getattr(workflow_run, 'generation_outputs_requested', None)
        requested = frozenset(normalize_generation_outputs(outputs))
        if 'calendar_events' not in requested:
            return

        spreadsheet_data = input_data.get('spreadsheet_data')
        if not spreadsheet_data:
            last_execution = workflow_run.step_executions.filter(
                status='completed',
            ).order_by('-step_order').first()
            if last_execution and last_execution.output_data:
                spreadsheet_data = last_execution.output_data.get('spreadsheet_data')

        if not spreadsheet_data:
            yield {
                'type': 'error',
                'content': 'Cannot suggest calendar events without spreadsheet data.',
            }
            return

        try:
            from core.services.gemini_client import _get_api_key as _gemini_key
            if not _gemini_key():
                yield {
                    'type': 'error',
                    'content': 'Calendar AI is not configured. Please set GEMINI_API_KEY.',
                }
                return
            result = _call_gemini_calendar_from_analysis(
                spreadsheet_data,
                workflow_run.analysis_result or {},
                user_id=str(self.user.id),
                success_criteria=workflow_run.success_criteria,
                agent_session=self.session,
            )
            events = result.get('calendar_events', [])
            yield {
                'type': 'calendar_events',
                'content': f'Suggested {len(events)} calendar event(s).',
                'data': result,
            }
        except GenerationValidationError as exc:
            logger.warning('Calendar generation validation failed: %s', exc)
            yield {
                'type': 'error',
                'content': 'Calendar suggestions could not be validated. Please try again.',
            }
        except Exception:
            logger.exception('Calendar generation from analysis failed')
            yield {
                'type': 'error',
                'content': 'Failed to generate calendar suggestions. Please try again.',
            }

    def _execute_steps(self, workflow_run, input_data):
        """Run steps in order. Pause on await_confirmation. Record AgentStepExecution."""
        from ..executors import get_executor
        from ..generation_registry import normalize_generation_outputs, should_skip_workflow_step
        from django.utils import timezone as tz

        steps = workflow_run.workflow_definition.steps.filter(
            order__gte=workflow_run.current_step_order, is_deleted=False,
        ).order_by('order')

        total_steps = workflow_run.workflow_definition.steps.filter(
            is_deleted=False
        ).count()
        current_data = input_data
        requested = frozenset(
            normalize_generation_outputs(input_data.get('generation_outputs'))
        )

        if not steps.exists():
            workflow_run.status = 'completed'
            workflow_run.save(update_fields=['status', 'updated_at'])
            yield {
                'type': 'text',
                'content': (
                    f'**{workflow_run.workflow_definition.name}** completed. '
                    'There are no further steps in this workflow.'
                ),
            }
            return

        for step in steps:
            if should_skip_workflow_step(step.step_type, requested):
                yield {
                    'type': 'step_progress',
                    'data': {
                        'step_order': step.order,
                        'step_name': step.name,
                        'step_type': step.step_type,
                        'status': 'skipped',
                        'total_steps': total_steps,
                    },
                }
                workflow_run.current_step_order = step.order + 1
                workflow_run.save(update_fields=['current_step_order'])
                continue

            execution = AgentStepExecution.objects.create(
                workflow_run=workflow_run,
                step=step,
                step_order=step.order,
                step_name=step.name,
                status='running',
                input_data=current_data,
                started_at=tz.now(),
            )

            yield {
                'type': 'step_progress',
                'data': {
                    'step_order': step.order,
                    'step_name': step.name,
                    'step_type': step.step_type,
                    'status': 'running',
                    'total_steps': total_steps,
                },
            }

            executor = get_executor(step, workflow_run, self)
            executor.step_execution = execution
            result = executor.execute(current_data)

            if result.success:
                if getattr(result, 'pause_external_approval', False):
                    execution.status = 'awaiting'
                    execution.save(update_fields=['status', 'updated_at'])
                    workflow_run.status = 'awaiting_external_approval'
                    workflow_run.save(update_fields=['status', 'updated_at'])
                    for event in result.sse_events:
                        yield event
                    return

                execution.status = 'completed'
                execution.output_data = result.output_data
                execution.completed_at = tz.now()
                execution.save()

                for event in result.sse_events:
                    yield event

                if step.step_type == 'analyze_data':
                    yield from self._emit_calendar_events_if_requested(
                        workflow_run, current_data
                    )

                # Pause on await_confirmation — persist status BEFORE yielding SSE
                # so a fast "Continue" click cannot race ahead of the DB write.
                if step.step_type == 'await_confirmation':
                    workflow_run.status = 'awaiting_confirmation'
                    workflow_run.current_step_order = step.order + 1
                    workflow_run.save(
                        update_fields=['status', 'current_step_order', 'updated_at']
                    )
                    for event in result.sse_events:
                        yield event
                    return

                current_data = result.output_data or current_data
            else:
                if result.skipped:
                    execution.status = 'skipped'
                    execution.completed_at = tz.now()
                    execution.save(update_fields=['status', 'updated_at'])

                    logger.warning("Workflow step skipped after retries exhausted: run_id=%s, step=%s, step_type=%s", workflow_run.id, step.name, step.step_type)

                    #Provide explanation for users to understand why this step didn't run
                    yield {
                        'type': 'text',
                        'content': f'The "{step.name}" step was skipped after retries. Continuing with the remaining steps.',
                    }
                    yield {
                        'type': 'step_progress',
                        'data': {
                            'step_order': step.order,
                            'step_name': step.name,
                            'step_type': step.step_type,
                            'status': 'skipped',
                            'total_steps': total_steps,
                        },
                    }
                    workflow_run.current_step_order = step.order + 1
                    workflow_run.save(update_fields=['current_step_order'])
                    continue
                else:
                    execution.status = 'failed'
                    execution.error_message = result.error
                    execution.completed_at = tz.now()
                    execution.save()

                    workflow_run.status = 'failed'
                    workflow_run.error_message = result.error
                    workflow_run.save()

                    yield {'type': 'error', 'content': result.error}
                    return


        workflow_run.status = 'completed'
        workflow_run.save(update_fields=['status', 'updated_at'])
        yield {
            'type': 'text',
            'content': (
                f'**{workflow_run.workflow_definition.name}** completed successfully.'
            ),
        }

    def _resume_workflow(self, workflow_run, extra_input=None):
        """Resume a paused workflow from the last completed step's output.

        extra_input is merged into the input data before execution, allowing
        callers to inject user-provided values (e.g. confirmed column_mapping).
        """
        last_execution = workflow_run.step_executions.filter(
            status='completed'
        ).order_by('-step_order').first()

        input_data = last_execution.output_data if last_execution else {}
        if extra_input:
            input_data = {**input_data, **extra_input}
        yield from self._execute_steps(workflow_run, input_data)

    def _legacy_confirm(self, action, workflow_run):
        """Backward compat: create_tasks for legacy runs."""
        if action == 'create_tasks':
            if workflow_run and workflow_run.analysis_result:
                yield from self.create_tasks_from_analysis(workflow_run)
            else:
                yield {"type": "error", "content": "No analysis found to create tasks from."}
        elif action == 'generate_miro':
            if not workflow_run or not workflow_run.analysis_result:
                yield {"type": "error", "content": "No analysis found to generate a Miro board from."}
                return
            try:
                outcome, locked_run, board_title = self._legacy_start_miro_background_if_needed(
                    workflow_run
                )
            except Exception as e:
                logger.exception(
                    "Failed to enqueue legacy Miro generation for workflow_run=%s",
                    getattr(workflow_run, 'id', workflow_run),
                )
                yield {"type": "error", "content": f"Failed to start Miro generation: {e}"}
                return

            if outcome == 'already_exists':
                logger.info(
                    "Generate Miro requested but board already exists for workflow_run=%s board=%s",
                    locked_run.id,
                    locked_run.miro_board_id,
                )
                yield {
                    "type": "text",
                    "content": f"Miro board already exists: {board_title}",
                }
                return

            yield {
                "type": "miro_status",
                "content": MIRO_LEGACY_BG_QUEUED_MESSAGE,
                "data": {"workflow_run_id": str(locked_run.id), "status": "running"},
            }

    def _legacy_handle(self, message, spreadsheet_id=None, csv_filename=None,
                       action=None, file_id=None):
        """Full legacy logic — preserves original handle_message behavior
        including the follow-up chat path."""
        if file_id:
            yield from self.analyze_file(file_id)
            yield {"type": "done"}
            return
        if action == 'analyze' and csv_filename:
            yield from self.analyze_csv(csv_filename)
        elif action == 'analyze' and spreadsheet_id:
            yield from self.analyze_spreadsheet(spreadsheet_id)
        elif action == 'create_tasks':
            workflow_run = self.session.workflow_runs.filter(
                analysis_result__isnull=False
            ).order_by('-created_at').first()
            if workflow_run and workflow_run.analysis_result:
                yield from self.create_tasks_from_analysis(workflow_run)
            else:
                yield {"type": "error", "content": "No analysis found to create tasks from."}
        else:
            # Follow-up chat path
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                chat_follow_up_started=True,
                chat_followed_up=False,
            ).order_by('-created_at').first()

            if latest_run:
                yield {"type": "text", "content": "Thinking..."}
                history = AgentMessage.objects.filter(
                    session=self.session
                ).order_by('created_at')
                chat_context = serialize_agent_messages(history)
                full_input = f"{chat_context}\n\n[user]: {message}"
                try:
                    from core.utils.bot_user import get_agent_bot_user

                    bot = get_agent_bot_user()
                    project_members = _serialize_project_members(
                        self.project,
                        excluded_users=[bot],
                    )
                    logger.info(
                        "Running agent follow-up chat for project=%s session=%s workflow_run=%s user=%s project_members=%s",
                        self.project.id,
                        self.session.id,
                        latest_run.id,
                        self.user.id,
                        len(project_members),
                    )
                    result = _call_gemini_chat(
                        full_input,
                        user_id=self.user.id,
                        analysis_result=latest_run.analysis_result,
                        project_members=project_members,
                        current_username=self.user.username or '',
                        agent_session=self.session,
                    )
                    follow_up_status = result.get("status", "completed")
                    reply = result.get("text") or result.get("reply", "")
                    forwards = result.get("forwards", [])
                    close_follow_up = follow_up_status == 'completed' or bool(forwards)
                    logger.info(
                        "Agent follow-up chat completed for workflow_run=%s status=%s forwards=%s close_follow_up=%s",
                        latest_run.id,
                        follow_up_status,
                        len(forwards),
                        close_follow_up,
                    )

                    if close_follow_up:
                        latest_run.chat_followed_up = True
                        latest_run.save(update_fields=['chat_followed_up'])
                    yield {"type": "text", "content": reply}

                    if forwards:
                        from ..approval_gate import KIND_FORWARD_MESSAGE, request_external_commit

                        gate = request_external_commit(
                            orchestrator=self,
                            workflow_run=latest_run,
                            step_execution=None,
                            kind=KIND_FORWARD_MESSAGE,
                            draft={'forwards': forwards},
                            commit_context={},
                        )
                        for ev in gate.sse_events:
                            yield ev
                except Exception as e:
                    logger.error(f"Dify chat call failed: {e}")
                    yield {"type": "error", "content": str(e)}
            else:
                yield {
                    "type": "text",
                    "content": (
                        "I can help you analyze spreadsheet data and recommended tasks. "
                        "To get started, select a spreadsheet and use the 'analyze' action."
                    ),
                }
        yield {"type": "done"}
