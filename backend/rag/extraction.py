"""
Source-document extraction for RAG indexing (MED-264).

Each extractor takes a live source row and returns a `SourceDocument` (the
full text to chunk/embed, plus enough metadata to detect changes and render
a citation), or `None` if the source should be excluded from the index
(soft-deleted, cancelled, unassigned, or reduces to no meaningful text at
all).

Extractors never decide *how* to index (chunking, embedding, staleness) —
that's `rag.indexing`'s job. They only answer "what is the current text of
this document, and does it even count as indexable right now." Extraction
must be deterministic: the same DB state must always produce the same
`text` (and therefore the same `content_hash`), so every child-row query
below has an explicit, fully-tiebroken `order_by()` that preserves each
model's intended business ordering rather than relying on `Meta.ordering`
alone (which is not always unique).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from meetings.models import Meeting
from notion_editor.models import ContentBlock, Draft, DraftProjectLink
from rag.models import DocumentSourceType
from retrospective.models import Insight, RetrospectiveStatus, RetrospectiveTask


def compute_content_hash(text: str) -> str:
    """sha256 of the exact text that will be chunked/embedded.

    Hashing the final text (not the source rows themselves) means "hash
    matches" provably implies "chunking would reproduce byte-identical
    chunks" — chunking is a pure function of this string.
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass
class SourceDocument:
    source_type: str
    source_id: str
    project_id: int
    text: str
    citation_metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: Optional[datetime] = None  # observability only, see DocumentIndexState
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.content_hash = compute_content_hash(self.text)


def _join_nonempty(parts: list[str]) -> str:
    return '\n\n'.join(p.strip() for p in parts if p and p.strip())


def extract_meeting(meeting: Meeting) -> Optional[SourceDocument]:
    """Meeting: title + objective + summary + MeetingDocument.content.

    Archived meetings are still indexed — `is_archived` is an explicit
    "immutable knowledge record" state in this codebase, not an exclusion.
    Only soft-deleted meetings are excluded.
    """
    if meeting.is_deleted:
        return None

    parts = [meeting.title, meeting.objective, meeting.summary]

    document = getattr(meeting, 'document', None)  # MeetingDocument OneToOne; None if never created
    if document is not None and document.content:
        parts.append(document.content)

    text = _join_nonempty(parts)
    if not text:
        return None

    return SourceDocument(
        source_type=DocumentSourceType.MEETING,
        source_id=str(meeting.id),
        project_id=meeting.project_id,
        text=text,
        citation_metadata={
            'title': meeting.title,
            'meeting_id': meeting.id,
            'scheduled_date': meeting.scheduled_date.isoformat() if meeting.scheduled_date else None,
        },
        updated_at=meeting.updated_at,
    )


def extract_notion_draft(draft: Draft) -> Optional[SourceDocument]:
    """Notion Draft: title + plain text of all content blocks, in order.

    Excluded if soft-deleted or unassigned (no DraftProjectLink) — RAG
    indexing must never guess a project for an unassigned draft. A plain
    filter().first() avoids depending on reverse-OneToOne descriptor
    exception semantics to detect "no link".
    """
    if draft.is_deleted:
        return None

    project_link = DraftProjectLink.objects.filter(draft_id=draft.pk).first()
    if project_link is None:
        return None

    # Preserves ContentBlock's intended business order (`order`, then
    # `created_at`) and adds `id` as a final unique tiebreaker.
    blocks = ContentBlock.objects.filter(draft=draft).order_by('order', 'created_at', 'id')
    block_texts = [block.get_text_content() for block in blocks]

    text = _join_nonempty([draft.title, *block_texts])
    if not text:
        return None

    return SourceDocument(
        source_type=DocumentSourceType.NOTION_DRAFT,
        source_id=str(draft.id),
        project_id=project_link.project_id,
        text=text,
        citation_metadata={
            'title': draft.title,
            'draft_id': draft.id,
        },
        updated_at=draft.updated_at,
    )


def _format_insight(insight: Insight) -> str:
    return f"- [{insight.get_severity_display()}] {insight.title}: {insight.description}"


def extract_retrospective(task: RetrospectiveTask) -> Optional[SourceDocument]:
    """RetrospectiveTask: semantic decision/outcome fields + active Insights.

    Cancelled retrospectives are excluded — there is no separate soft-delete
    flag on this model, `CANCELLED` is the terminal "never happened" state.
    """
    if task.status == RetrospectiveStatus.CANCELLED:
        return None

    lines = [
        f"Decision: {task.decision}",
        f"Confidence level: {task.confidence_level}/5",
        f"Primary assumption: {task.primary_assumption}",
    ]
    if task.key_risk_ignore:
        lines.append(f"Key risk (ignored): {task.key_risk_ignore}")
    # Nullable TextChoices CharFields, not booleans — `None` means "not yet
    # answered" and must be distinguished from a real choice value via
    # `is not None`, not truthiness (a future falsy-but-real choice value
    # would otherwise be silently dropped from the extracted text).
    if task.outcome_compared_to_expectation is not None:
        lines.append(f"Outcome compared to expectation: {task.get_outcome_compared_to_expectation_display()}")
    if task.would_make_same_decision_again is not None:
        lines.append(f"Would make the same decision again: {task.get_would_make_same_decision_again_display()}")
    if task.biggest_wrong_assumption:
        lines.append(f"Biggest wrong assumption (post-outcome): {task.biggest_wrong_assumption}")

    # Preserves Insight's intended business ordering (severity desc, then
    # newest first) and adds `pk` as a unique tiebreaker — Meta.ordering
    # alone is not guaranteed unique (bulk rule-engine inserts can tie on
    # created_at).
    insights = list(
        Insight.objects.filter(retrospective=task, is_active=True)
        .order_by('-severity', '-created_at', 'pk')
    )
    if insights:
        lines.append('')
        lines.append('Insights:')
        lines.extend(_format_insight(i) for i in insights)

    text = _join_nonempty(['\n'.join(lines)])
    if not text:
        return None

    latest_child_update = max((i.updated_at for i in insights), default=task.updated_at)

    return SourceDocument(
        source_type=DocumentSourceType.RETROSPECTIVE,
        source_id=str(task.id),
        project_id=task.campaign_id,
        text=text,
        citation_metadata={
            # `title` is the citation's human-readable identity/label, not
            # its content. RetrospectiveTask has no dedicated title field --
            # `decision` is evidentiary content (what was decided), not a
            # stable display label, so it must not be repurposed as one.
            # The model's own __str__ is the existing, already-defined
            # display representation (campaign name + status), which is a
            # deterministic label derived from real fields rather than an
            # invented one.
            'title': str(task),
            'decision': task.decision,
            'retrospective_id': str(task.id),
        },
        updated_at=max(task.updated_at, latest_child_update),
    )


# source_type -> model class, for looking up the live row before extracting.
SOURCE_MODELS: dict[str, type] = {
    DocumentSourceType.MEETING: Meeting,
    DocumentSourceType.NOTION_DRAFT: Draft,
    DocumentSourceType.RETROSPECTIVE: RetrospectiveTask,
}

SOURCE_EXTRACTORS: dict[str, Callable[[Any], Optional[SourceDocument]]] = {
    DocumentSourceType.MEETING: extract_meeting,
    DocumentSourceType.NOTION_DRAFT: extract_notion_draft,
    DocumentSourceType.RETROSPECTIVE: extract_retrospective,
}


def extract_source(source_type: str, source_id: str) -> Optional[SourceDocument]:
    """Look up the live row by (source_type, source_id) and extract it.

    Returns None uniformly when: the row is gone entirely (hard-deleted),
    the extractor decides the source is excluded, or the source reduces to
    no meaningful text — callers treat all three identically.
    """
    model = SOURCE_MODELS[source_type]
    extractor = SOURCE_EXTRACTORS[source_type]
    try:
        instance = model.objects.get(pk=source_id)
    except model.DoesNotExist:
        return None
    return extractor(instance)
