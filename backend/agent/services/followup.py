"""Follow-up chat after an analysis: LLM chat calls and follow-up lifecycle."""
import json
import logging

from ..agent_utils import json_input
from .common import _coerce_json

logger = logging.getLogger(__name__)


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


def _call_ollama_chat(
    chat_messages,
    user_id=None,
    analysis_result=None,
    project_members=None,
    current_username='',
    agent_session=None,
):
    """Call Ollama for post-analysis follow-up. Replaces _call_dify_chat."""
    from core.services.ollama_client import call_ollama_json

    user_prompt = (
        f"Chat history:\n  {chat_messages}\n\n"
        f"Analysis result JSON:\n  {json_input(analysis_result) if analysis_result else '{}'}\n\n"
        f"Project members JSON:\n  {json_input(project_members or [])}\n\n"
        f"Current username:\n  {current_username or ''}\n\n"
        f"Return valid JSON only."
    )

    try:
        if agent_session is None:
            parsed = call_ollama_json(
                system_prompt=_FOLLOWUP_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.5,
                timeout=120,
            )
        else:
            from ..llm_client import call_llm as _call_llm_unified

            result = _call_llm_unified(
                agent_session=agent_session,
                system_prompt=_FOLLOWUP_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.5,
                max_output_tokens=4096,
                response_mime_type='application/json',
                call_purpose='follow_up_chat',
            )
            parsed = json.loads(result['text'])
    except Exception as e:
        logger.error("Ollama chat call failed: %s", e)
        raise RuntimeError(f"Ollama chat failed: {e}") from e

    normalized = _normalize_llm_chat_output(parsed)
    if normalized:
        return normalized

    raise RuntimeError("LLM chat returned unexpected output format")


class FollowUpMixin:
    """Follow-up lifecycle for AgentOrchestrator workflow runs."""

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
