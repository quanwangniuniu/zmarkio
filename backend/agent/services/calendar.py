"""Calendar Q&A, event creation and analysis-driven calendar suggestions."""
import json
import logging

from django.conf import settings
from core.services.ollama_client import parse_json_text
from django.utils import timezone as django_timezone

from .analysis import _preprocess_spreadsheet

logger = logging.getLogger(__name__)


def _call_ollama_calendar_from_analysis(
    spreadsheet_data,
    analysis_result,
    user_id=None,
    success_criteria=None,
    user_context=None,
    agent_session=None,
):
    """Suggest calendar events from spreadsheet + analysis context."""
    from core.services.ollama_client import call_ollama_json
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
        raw = call_ollama_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
        )
    else:
        from ..llm_client import call_llm as _call_llm_unified

        result = _call_llm_unified(
            agent_session=agent_session,
            provider='ollama',
            model=settings.AGENT_LLM_MODEL,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            max_output_tokens=4096,
            response_mime_type='application/json',
            call_purpose='calendar_suggestion',
        )
        raw = parse_json_text(result['text'])
    logger.info("Calling Ollama for calendar events user_id=%s", user_id)
    return validate_calendar_events_response(raw)


class CalendarMixin:
    """Calendar entry points for AgentOrchestrator."""

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

        # Call Ollama calendar assistant
        from core.services.ollama_client import call_ollama_text, is_llm_configured
        if not is_llm_configured():
            yield {"type": "error", "content": "Calendar AI is not configured. Please set OLLAMA_BASE_URL."}
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
            raw_answer = call_ollama_text(
                system_prompt=_calendar_system_prompt,
                user_prompt=(
                    f"Calendar data:\n{calendar_data_str}\n\n"
                    f"User question: {message}\n\n"
                    f"Return JSON only."
                ),
                temperature=0.3,
            )
        except Exception as e:
            logger.error(f"Ollama calendar workflow error: {e}")
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

    def _emit_calendar_events_if_requested(self, workflow_run, input_data):
        """After workflow steps, optionally call Ollama for calendar_events."""
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
            from core.services.ollama_client import is_llm_configured
            if not is_llm_configured():
                yield {
                    'type': 'error',
                    'content': 'Calendar AI is not configured. Please set OLLAMA_BASE_URL.',
                }
                return
            result = _call_ollama_calendar_from_analysis(
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
