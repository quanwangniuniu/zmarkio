"""Read-only Q&A over a user's draft content."""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class DraftMixin:
    """Draft Q&A entry point for AgentOrchestrator."""

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
        # Cap on the draft text inlined into the LLM context (settings.AGENT_DRAFT_PAYLOAD_MAX_CHARS).
        max_chars = settings.AGENT_DRAFT_PAYLOAD_MAX_CHARS
        if len(draft_text) > max_chars:
            draft_text = draft_text[:max_chars].rstrip() + "\n\n[... draft truncated for length ...]"

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
