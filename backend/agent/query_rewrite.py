"""Project-RAG query rewrite.

The current question is already stored as the latest user message before
answer_project_question runs. recent_rewrite_turns selects earlier completed
turns. rewrite_project_question calls the rewrite model only when that
history exists, and returns the original question when the call cannot be
used.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

from agent.models import AgentMessage

logger = logging.getLogger(__name__)

_REWRITE_MODEL = 'gemini-2.5-flash-lite'
_REWRITE_TEMPERATURE = 0
_REWRITE_MAX_OUTPUT_TOKENS = 256
_REWRITE_SYSTEM_PROMPT = (
    "You rewrite a follow-up question so it can be searched on its own. "
    "When prior turns clearly identify a referent, resolve references such as "
    "'that', 'she', or 'why' into a complete standalone question. "
    "Only in that clear-referent case, make the rewrite self-contained and "
    "searchable: pull in key nouns from the most recent relevant turn "
    "(entity names, document or asset types, people, codes) — do not leave "
    "only a bare label such as 'Option B' or 'that version' without the "
    "surrounding nouns that identify it (e.g. homepage banner, Priya, "
    "welcome email). "
    "When the referent is ambiguous or the conversation does not uniquely "
    "identify what the user means, return the follow-up unchanged — do not "
    "guess, pick among alternatives, invent facts, or force a searchable "
    "rewrite. "
    "If the question is already complete, or it changes topic away from the "
    "prior turns, return it unchanged — do not append nouns from earlier turns. "
    "Return one line containing only the question. Do not explain."
)


@dataclass(frozen=True)
class RewriteResult:
    """Outcome of rewrite_project_question_result.

    fell_back is True only when the model was needed but its output could not
    be used (error / timeout / unusable text), so the original question is
    kept. Returning the original because there is no history, or because the
    model intentionally left a complete question unchanged, is not a fallback.
    """

    question: str
    fell_back: bool
    reason: str


def recent_rewrite_turns(session) -> list[tuple[str, str]]:
    """Return the latest completed (user, assistant) turns before the current question.

    Messages are read from newest to oldest. The newest user message is the
    question being asked now, so it and anything after it are skipped. Walking
    backward, a turn is that user message plus the assistant text that follows
    it. Collection stops once RAG_QUERY_REWRITE_HISTORY_TURNS completed turns
    are in hand, so a long session is not read to the beginning.

    System messages and non-text message types are left out. Assistant text
    is truncated to RAG_QUERY_REWRITE_ASSISTANT_MAX_CHARS. The returned turns
    are oldest-first within the window.

    An empty list means there is no earlier completed turn, so the caller
    should search with the original question and skip the rewrite model.
    """
    turns_limit = settings.RAG_QUERY_REWRITE_HISTORY_TURNS
    max_chars = settings.RAG_QUERY_REWRITE_ASSISTANT_MAX_CHARS
    if turns_limit < 1:
        return []

    newest_first = (
        AgentMessage.objects.filter(
            session=session,
            role__in=('user', 'assistant'),
            message_type='text',
            is_deleted=False,
        )
        .order_by('-created_at', '-id')
        .iterator(chunk_size=turns_limit * 4)
    )

    seen_current_question = False # Whether the current question has already been skipped
    assistant_parts: list[str] = [] # Temporarily store assistant response parts
    turns_newest_first: list[tuple[str, str]] = [] # Store complete user–assistant turns

    for message in newest_first:
        content = (message.content or '').strip()
        if not content:
            continue
        if not seen_current_question:
            if message.role == 'user':
                seen_current_question = True
            continue
        if message.role == 'assistant':
            assistant_parts.append(content)
            continue
        if not assistant_parts:
            continue
        assistant_text = '\n'.join(reversed(assistant_parts))
        assistant_parts = []
        turns_newest_first.append((content, assistant_text[:max_chars]))
        if len(turns_newest_first) >= turns_limit:
            break

    turns_newest_first.reverse()
    return turns_newest_first


def rewrite_project_question(session, project, question: str) -> str:
    """Return the question to search with. See rewrite_project_question_result."""
    return rewrite_project_question_result(session, project, question).question


def rewrite_project_question_result(session, project, question: str) -> RewriteResult:
    """Rewrite with explicit fallback metadata for eval and debugging.

    With no earlier completed turn, the original question is returned and
    the model is not called. Otherwise the model must return exactly one
    line. An empty reply, more than one line, a timeout, or any other
    error returns the original question with fell_back=True.
    """
    original = (question or '').strip()
    turns = recent_rewrite_turns(session)
    if not turns:
        _log_rewrite(project, session, original, original, fell_back=False, reason='no_history')
        return RewriteResult(question=original, fell_back=False, reason='no_history')

    try:
        from agent.llm_client import call_llm

        result = call_llm(
            agent_session=session,
            provider='gemini',
            model=_REWRITE_MODEL,
            system_prompt=_REWRITE_SYSTEM_PROMPT,
            user_prompt=_rewrite_user_prompt(turns, original),
            max_output_tokens=_REWRITE_MAX_OUTPUT_TOKENS,
            temperature=_REWRITE_TEMPERATURE,
            call_purpose='other',
            timeout=settings.RAG_QUERY_REWRITE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "RAG query rewrite failed project_id=%s session_id=%s error_type=%s",
            project.id, session.id, type(exc).__name__,
        )
        _log_rewrite(project, session, original, original, fell_back=True, reason='error')
        return RewriteResult(question=original, fell_back=True, reason='error')

    accepted = _single_question(result.get('text') or '')
    if accepted is None:
        _log_rewrite(project, session, original, original, fell_back=True, reason='unusable_output')
        return RewriteResult(question=original, fell_back=True, reason='unusable_output')

    _log_rewrite(project, session, original, accepted, fell_back=False, reason='rewritten')
    return RewriteResult(question=accepted, fell_back=False, reason='rewritten')


# Synthetic few-shots only. Keep them out of the eval fixture so the model
# cannot memorize fixture wording; they teach clear vs ambiguous, and that
# rewrites must stay searchable (not bare labels).
_REWRITE_FEW_SHOTS = """\
Examples (follow these patterns exactly):

Example 1 — clear referent, rewrite with searchable nouns:
Conversation:
User: Which vendor did we choose for email?
Assistant: We chose Vendor A for the outbound email platform.
Follow-up question:
Why was that?
Rewritten:
Why did we choose Vendor A for the outbound email platform?

Example 2 — too short (wrong) vs searchable (right):
Conversation:
User: Which homepage banner option did Priya recommend?
Assistant: Priya recommended Option B as the strongest fit for the brand guidelines.
Follow-up question:
Did the client approve that option?
Wrong (too short — bare label only):
Did the client approve Option B?
Rewritten:
Did the client approve homepage banner Option B that Priya recommended?

Example 3 — two candidates, leave unchanged:
Conversation:
User: Which launches are on the calendar?
Assistant: The product landing page and the sales-deck launch were both discussed.
Follow-up question:
When is that launch?
Rewritten:
When is that launch?

Example 4 — two drafts, leave unchanged:
Conversation:
User: What email drafts exist?
Assistant: There is a rejected v1 draft and an approved v2 revision.
Follow-up question:
What promo code is in that email?
Rewritten:
What promo code is in that email?

Example 5 — unique topic, resolve referent (facts may still be unknown):
Conversation:
User: What pricing topics came up?
Assistant: Next-year pricing was mentioned, but numbers were deferred to finance.
Follow-up question:
What are the finalized numbers for that?
Rewritten:
What are the finalized numbers for next-year pricing?

Example 6 — already complete, leave unchanged (do not graft prior nouns):
Conversation:
User: What campaign budget was confirmed for Q4?
Assistant: A campaign budget of $85,000 for Q4.
Follow-up question:
What purchase order number was confirmed for the holiday gift-card inserts?
Wrong (grafted prior budget):
What purchase order number was confirmed for the holiday gift-card inserts for the $85,000 Q4 budget?
Rewritten:
What purchase order number was confirmed for the holiday gift-card inserts?
"""


def _rewrite_user_prompt(turns: list[tuple[str, str]], question: str) -> str:
    lines = []
    for user_text, assistant_text in turns:
        lines.append(f"User: {user_text}")
        lines.append(f"Assistant: {assistant_text}")
    history = "\n".join(lines)
    return (
        f"{_REWRITE_FEW_SHOTS}\n"
        f"Now rewrite this follow-up. Return one line only. If the referent is "
        f"clear, emit a searchable standalone question with key nouns (not a "
        f"bare-label rewrite like the Wrong line in Example 2). If ambiguous "
        f"or already complete, return the follow-up unchanged (see Examples "
        f"3–4 and 6) — do not graft prior-turn nouns.\n\n"
        f"Conversation:\n{history}\n\n"
        f"Follow-up question:\n{question}\n"
        f"Rewritten:"
    )


def _single_question(text: str) -> str | None:
    """Accept one non-empty line. Several lines are an explanation, not a question."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    line = lines[0]
    if len(line) >= 2 and line[0] == line[-1] and line[0] in {'"', "'"}:
        line = line[1:-1].strip()
    return line or None


def _log_rewrite(project, session, original: str, rewritten: str, *, fell_back: bool, reason: str) -> None:
    logger.info(
        "RAG query rewrite project_id=%s session_id=%s fell_back=%s reason=%s original=%s rewritten=%s",
        project.id, session.id, fell_back, reason, original, rewritten,
    )
