"""
Management command: run_project_rag_eval (MED-264 Phase 2 eval harness).

Seeds the Phase 1 synthetic fixture (rag/tests/fixtures/project_rag_eval_dataset.json)
into real Meeting/Draft/RetrospectiveTask rows inside a dedicated eval tenant,
runs the real indexing pipeline (real chunking + real Gemini embeddings) and
the real rag.retrieval.retrieve_chunks() pgvector path, then scores
evidence-aware retrieval quality separately per question type.

Baseline retrieval parameters (chunk_size=1500, overlap=200, exact cosine, no
reranker/hybrid/ANN) are the untouched settings.py defaults -- this command
never overrides them. `top_k` is passed explicitly to retrieve_chunks() as an
eval-time argument only; it does not touch settings.RAG_RETRIEVAL_TOP_K.

Tenant schema handling
-----------------------
This app is schema-per-organization (see core.tenant_config /
core.services.tenant). Project, Meeting, MeetingDocument, Draft, ContentBlock,
DraftProjectLink, DocumentChunk and DocumentIndexState are all tenant models,
physically present only inside each org's own `org_<slug>` schema -- never in
`public`. A management command process starts on `search_path = public` with
nothing to change that, unlike an HTTP request (TenantSchemaMiddleware) or a
Celery task (rag.tasks wraps every call in tenant_schema_context itself). So
the Organization/User are created first against `public` (which is also what
auto-provisions the eval org's schema, via Organization.save()), then
everything else -- Project, all source seeding, all indexing, all retrieval --
runs inside one `tenant_schema_context(eval_schema)` block, exactly mirroring
rag/tasks.py's own convention. RetrospectiveTask/Insight are the one source
type that stays in `public` permanently (not in get_tenant_models()), which
resolves fine either way since `SET search_path TO org_x, public` still falls
through to `public` for tables that don't exist in the org schema.

RAG-enqueue suppression during seeding
----------------------------------------
Creating/updating Meeting/MeetingDocument/Draft/ContentBlock/DraftProjectLink/
RetrospectiveTask rows fires the MED-264 post_save signals (meetings/signals.py,
notion_editor/signals.py, retrospective/signals.py), which all funnel into
`rag.tasks.index_document_task.delay(...)` via a transaction.on_commit closure.
Left alone, seeding would race real synchronous `index_source_document()` calls
below against background Celery enqueues of the exact same work. Patching
`rag.tasks.index_document_task.delay` for the seeding phase is the smallest
correct fix: it's a single choke point shared by all three apps' signal
closures (all do `from rag.tasks import index_document_task` locally, which
always resolves to the same module-level Task object), it leaves every signal
receiver itself connected and running, and it's the same patch target
rag/tests/test_signals.py already uses for the identical reason.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string

from core.models import Organization, Project
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from meetings.models import Meeting, MeetingDocument
from meetings.services import ensure_meeting_type_definition
from notion_editor.models import ContentBlock, Draft, DraftProjectLink
from rag.indexing import IndexOutcome, index_source_document
from rag.models import DocumentSourceType
from rag.retrieval import RetrievedChunk, retrieve_chunks
from retrospective.models import RetrospectiveTask

User = get_user_model()

_COMMAND_DIR = Path(__file__).resolve().parent
_RAG_APP_DIR = _COMMAND_DIR.parents[1]  # backend/rag
DEFAULT_FIXTURE = _RAG_APP_DIR / 'tests' / 'fixtures' / 'project_rag_eval_dataset.json'
DEFAULT_OUTPUT_DIR = _RAG_APP_DIR / 'eval_results'

EVAL_ORG_NAME = 'RAG Eval Harness'
EVAL_ORG_EMAIL_DOMAIN = 'rag-eval-harness.internal'
EVAL_USER_USERNAME = 'rag_eval_harness_bot'
EVAL_USER_EMAIL = 'rag-eval-harness-bot@rag-eval-harness.internal'
EVAL_PROJECT_NAME = 'MED-264 RAG Eval Fixture'

EXPECTED_SOURCE_COUNTS = {'meeting': 12, 'notion_draft': 8, 'retrospective': 6}
EXPECTED_QUESTION_COUNTS = {'single-source': 14, 'multi-source': 6, 'no-answer': 2}

# Separator used only to join multiple chunks' content before a substring
# check, so a span can never appear to be "covered" purely by two chunks'
# text accidentally concatenating across a boundary (real text never
# contains a NUL byte).
_JOIN_SEP = '\x00'


# ---------------------------------------------------------------------------
# Fixture loading / validation
# ---------------------------------------------------------------------------

def _load_fixture(path: Path) -> dict:
    if not path.exists():
        raise CommandError(f"Fixture not found: {path}")
    try:
        with path.open(encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise CommandError(f"Fixture at {path} is not valid JSON: {exc}") from exc


def _validate_fixture_shape(data: dict, path: Path) -> None:
    """Validate source/question counts against this fixture's own declared
    shape (`expected_source_counts` / `expected_question_counts`) if it
    declares one, else against the canonical MED-264 Phase-1 dataset shape
    (EXPECTED_SOURCE_COUNTS / EXPECTED_QUESTION_COUNTS). This is purely
    additive: the canonical fixture has never declared those keys, so its
    validation behavior is byte-for-byte unchanged. A variant fixture (e.g.
    the no-retro partial-baseline one) declares its own expected counts so
    it gets the same "refuse to run against an unexpected dataset shape"
    guard, just against a different expected shape.
    """
    sources = data.get('sources', [])
    questions = data.get('questions', [])
    expected_source_counts = data.get('expected_source_counts', EXPECTED_SOURCE_COUNTS)
    expected_question_counts = data.get('expected_question_counts', EXPECTED_QUESTION_COUNTS)

    source_counts: dict[str, int] = {}
    for s in sources:
        source_counts[s['source_type']] = source_counts.get(s['source_type'], 0) + 1
    if source_counts != expected_source_counts:
        raise CommandError(
            f"Fixture {path} source counts {source_counts} do not match expected "
            f"{expected_source_counts} -- refusing to run against an unexpected dataset shape."
        )

    question_counts: dict[str, int] = {}
    for q in questions:
        question_counts[q['question_type']] = question_counts.get(q['question_type'], 0) + 1
    if question_counts != expected_question_counts:
        raise CommandError(
            f"Fixture {path} question counts {question_counts} do not match expected "
            f"{expected_question_counts} -- refusing to run against an unexpected dataset shape."
        )

    refs = [s['source_ref'] for s in sources]
    if len(refs) != len(set(refs)):
        raise CommandError(f"Fixture {path} has duplicate source_ref values.")


def _validate_question_refs(questions: list[dict], source_ref_map: dict[str, tuple[str, str]]) -> None:
    errors = []
    for q in questions:
        for exp in q.get('expected_sources', []):
            ref = exp['source_ref']
            if ref not in source_ref_map:
                errors.append(f"{q['id']}: expected_sources references unknown source_ref {ref!r}")
                continue
            seeded_type, _ = source_ref_map[ref]
            if seeded_type != exp['source_type']:
                errors.append(
                    f"{q['id']}: source_ref {ref!r} seeded as {seeded_type!r} but "
                    f"question expects source_type {exp['source_type']!r}"
                )
        for rel in q.get('related_but_unresolved_sources', []):
            if rel['source_ref'] not in source_ref_map:
                errors.append(f"{q['id']}: related_but_unresolved_sources references unknown source_ref {rel['source_ref']!r}")
    if errors:
        raise CommandError("Fixture/seed mismatch before any retrieval was attempted:\n" + "\n".join(errors))


# ---------------------------------------------------------------------------
# Seeding: find-or-synchronize each source's real model rows
# ---------------------------------------------------------------------------

def _sync_meeting(project: Project, src: dict) -> Meeting:
    type_def = ensure_meeting_type_definition(project, 'planning')
    meeting, _created = Meeting.objects.get_or_create(
        project=project,
        title=src['title'],
        defaults={'type_definition': type_def, 'objective': '', 'summary': ''},
    )
    document = getattr(meeting, 'document', None)
    if document is None:
        MeetingDocument.objects.create(meeting=meeting, content=src['content'])
    elif document.content != src['content']:
        document.content = src['content']
        document.save(update_fields=['content'])
    return meeting


def _sync_draft(project: Project, user, src: dict) -> Draft:
    draft, _created = Draft.objects.get_or_create(
        user=user,
        title=src['title'],
        defaults={'status': 'draft'},
    )
    DraftProjectLink.objects.get_or_create(draft=draft, defaults={'project': project})
    block, created = ContentBlock.objects.get_or_create(
        draft=draft,
        order=0,
        defaults={'block_type': 'text', 'content': {'text': src['content']}},
    )
    if not created and block.content.get('text') != src['content']:
        block.content = {'text': src['content']}
        block.save(update_fields=['content'])
    return draft


def _sync_retrospective(project: Project, user, src: dict) -> RetrospectiveTask:
    task, created = RetrospectiveTask.objects.get_or_create(
        campaign=project,
        decision=src['title'],
        defaults={'created_by': user, 'primary_assumption': src['content']},
    )
    if not created and task.primary_assumption != src['content']:
        task.primary_assumption = src['content']
        task.save(update_fields=['primary_assumption'])
    return task


_SYNC_DISPATCH = {
    DocumentSourceType.MEETING: lambda project, user, src: _sync_meeting(project, src),
    DocumentSourceType.NOTION_DRAFT: lambda project, user, src: _sync_draft(project, user, src),
    DocumentSourceType.RETROSPECTIVE: lambda project, user, src: _sync_retrospective(project, user, src),
}


def _seed_and_index(project: Project, user, sources: list[dict]) -> tuple[dict[str, tuple[str, str]], list[dict]]:
    """Find-or-synchronize every fixture source's real row, then run the real
    indexing pipeline on it. RAG-enqueue suppression is active for the whole
    block: both the synchronize step (ordinary ORM .save() calls, which fire
    signals) and the explicit index_source_document() calls below (unaffected
    either way, since they never go through rag.tasks at all).
    """
    source_ref_map: dict[str, tuple[str, str]] = {}
    outcomes: list[dict] = []

    with patch('rag.tasks.index_document_task.delay'):
        for src in sources:
            source_type = src['source_type']
            sync_fn = _SYNC_DISPATCH.get(source_type)
            if sync_fn is None:
                raise CommandError(f"Unknown source_type {source_type!r} for source_ref {src['source_ref']!r}")

            row = sync_fn(project, user, src)
            source_id = str(row.pk)
            source_ref_map[src['source_ref']] = (source_type, source_id)

            result = index_source_document(project.id, source_type, source_id)
            outcomes.append({
                'source_ref': src['source_ref'],
                'source_type': source_type,
                'source_id': source_id,
                'outcome': result.outcome.value,
                'detail': result.detail,
            })

    return source_ref_map, outcomes


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _reconstruct_source_text(chunks_by_index: dict[int, str]) -> str:
    """Stitch retrieved chunks for one source into de-duplicated text, so an
    evidence span that legitimately straddles a chunk boundary can still
    match -- without ever fabricating a match out of two unrelated chunks.

    Only chunks whose `chunk_index` values are exactly adjacent (i, i+1) are
    merged, and only after verifying the actual overlap between their
    content (the longest suffix of chunk i that equals a prefix of chunk
    i+1), searched over the full possible range up to
    `min(len(prev_chunk), len(next_chunk))` -- not capped by
    settings.RAG_CHUNK_OVERLAP, so this stays correct however chunk_size/
    overlap are configured, including a document indexed under a different
    pipeline than whatever the current settings say. If no verified overlap
    is found for an adjacent pair, the two chunks are treated as a boundary
    and never concatenated -- the scorer must not guess. Non-adjacent
    chunks (or indices that were never both retrieved) are joined with a
    NUL separator so a span can never appear to match purely from two
    arbitrary chunks concatenating.
    """
    if not chunks_by_index:
        return ''

    indices = sorted(chunks_by_index)
    runs: list[str] = [chunks_by_index[indices[0]]]

    for idx in indices[1:]:
        prev_idx = idx - 1
        if prev_idx not in chunks_by_index:
            runs.append(chunks_by_index[idx])
            continue

        prev_chunk = chunks_by_index[prev_idx]
        next_chunk = chunks_by_index[idx]
        overlap_len = min(len(prev_chunk), len(next_chunk))
        merge_tail = None
        while overlap_len > 0:
            if prev_chunk[-overlap_len:] == next_chunk[:overlap_len]:
                merge_tail = next_chunk[overlap_len:]
                break
            overlap_len -= 1

        if merge_tail is not None:
            # runs[-1] currently ends with prev_chunk verbatim (either it IS
            # prev_chunk, or a prior merge appended prev_chunk's own tail
            # onto it unmodified) -- safe to append just the new, non-
            # overlapping suffix of next_chunk.
            runs[-1] += merge_tail
        else:
            runs.append(next_chunk)

    return _JOIN_SEP.join(runs)


def _evidence_complete_rank(evidence: list[str], source_type: str, source_id: str, retrieved: list[RetrievedChunk]) -> int | None:
    """Smallest 1-indexed rank K such that the chunks ranked 1..K belonging to
    (source_type, source_id), reconstructed via `_reconstruct_source_text`,
    jointly contain every string in `evidence` as a literal substring. None
    if never satisfied within `retrieved`.
    """
    chunks_by_index: dict[int, str] = {}
    for rank, chunk in enumerate(retrieved, start=1):
        if chunk.source_type == source_type and chunk.source_id == source_id:
            chunks_by_index[chunk.chunk_index] = chunk.content
            combined = _reconstruct_source_text(chunks_by_index)
            if all(span in combined for span in evidence):
                return rank
    return None


def _evidence_coverage(evidence: list[str], source_type: str, source_id: str, retrieved: list[RetrievedChunk]) -> dict[str, bool]:
    """Per-span coverage across ALL retrieved chunks for this source (not
    just up to the completion rank) -- diagnostic visibility even when a
    source is never fully covered.
    """
    chunks_by_index = {
        chunk.chunk_index: chunk.content
        for chunk in retrieved
        if chunk.source_type == source_type and chunk.source_id == source_id
    }
    combined = _reconstruct_source_text(chunks_by_index)
    return {span: span in combined for span in evidence}


def _first_relevant_rank(expected_keys: set[tuple[str, str]], retrieved: list[RetrievedChunk]) -> int | None:
    for rank, chunk in enumerate(retrieved, start=1):
        if (chunk.source_type, chunk.source_id) in expected_keys:
            return rank
    return None


def _evidence_relevant_count_at_k(question_expected: list[dict], source_ref_map: dict[str, tuple[str, str]], retrieved: list[RetrievedChunk], k: int) -> int:
    """# of the top-k retrieved chunks that are evidence-relevant, using the
    SAME boundary-aware reconstruction semantics as evidence_complete_rank
    (via `_reconstruct_source_text`) -- not just `span in chunk.content` in
    isolation. A chunk counts iff adding it to that source's retrieved-so-far
    chunk set causes at least one previously-uncovered evidence span to
    become covered in the *reconstructed* text, so a span legitimately split
    across an adjacent chunk boundary is recognized by Precision exactly the
    way it's already recognized by Recall -- the same evidence can't be
    relevant for one and irrelevant for the other.
    """
    expected_by_key = {source_ref_map[exp['source_ref']]: exp for exp in question_expected}
    chunks_by_index_per_source: dict[tuple[str, str], dict[int, str]] = {key: {} for key in expected_by_key}

    relevant = 0
    for chunk in retrieved[:k]:
        key = (chunk.source_type, chunk.source_id)
        exp = expected_by_key.get(key)
        if exp is None:
            continue

        chunks_by_index = chunks_by_index_per_source[key]
        before = _reconstruct_source_text(chunks_by_index)
        before_covered = {span for span in exp['evidence'] if span in before}

        chunks_by_index[chunk.chunk_index] = chunk.content
        after = _reconstruct_source_text(chunks_by_index)
        after_covered = {span for span in exp['evidence'] if span in after}

        if after_covered - before_covered:
            relevant += 1
    return relevant


def _source_match_count_at_k(expected_keys: set[tuple[str, str]], retrieved: list[RetrievedChunk], k: int) -> int:
    return sum(1 for chunk in retrieved[:k] if (chunk.source_type, chunk.source_id) in expected_keys)


def _chunk_to_dict(rank: int, chunk: RetrievedChunk, source_ref_map_reverse: dict[tuple[str, str], str]) -> dict:
    key = (chunk.source_type, chunk.source_id)
    return {
        'rank': rank,
        'source_ref': source_ref_map_reverse.get(key, '<unseeded>'),
        'source_type': chunk.source_type,
        'source_id': chunk.source_id,
        'chunk_index': chunk.chunk_index,
        'similarity': round(chunk.similarity, 4),
        'content_snippet': chunk.content[:200],
    }


def _missing_source_category(source_type: str, source_id: str, retrieved: list[RetrievedChunk]) -> str:
    """Only meaningful when evidence_complete_rank is None. Distinguishes two
    different retrieval failure modes so a fail_reason doesn't conflate them:
    'source_not_retrieved' -- no chunk from this source appears anywhere
    among the retrieved chunks at all (embedding/ranking never surfaced the
    document); 'evidence_incomplete' -- at least one chunk from this source
    WAS retrieved, but the required evidence still isn't fully covered even
    combining every retrieved chunk for it (found the document, not enough
    of the answer within it).
    """
    was_retrieved = any(c.source_type == source_type and c.source_id == source_id for c in retrieved)
    return 'evidence_incomplete' if was_retrieved else 'source_not_retrieved'


def _score_single_source(question: dict, retrieved: list[RetrievedChunk], source_ref_map: dict[str, tuple[str, str]]) -> dict:
    exp = question['expected_sources'][0]
    source_type, source_id = source_ref_map[exp['source_ref']]
    rank = _evidence_complete_rank(exp['evidence'], source_type, source_id, retrieved)
    coverage = _evidence_coverage(exp['evidence'], source_type, source_id, retrieved)

    outcome = 'pass' if rank is not None else 'fail'
    fail_category = None
    fail_reason = None
    if outcome == 'fail':
        fail_category = _missing_source_category(source_type, source_id, retrieved)
        if fail_category == 'source_not_retrieved':
            fail_reason = f"expected source {exp['source_ref']} (source_not_retrieved): no chunk from it appears among top {len(retrieved)} retrieved chunks"
        else:
            fail_reason = (
                f"expected source {exp['source_ref']} (evidence_incomplete): retrieved, but required evidence "
                f"not fully covered -- uncovered spans: {[s for s, ok in coverage.items() if not ok]}"
            )

    return {
        'evidence_complete_rank': rank,
        'per_expected_source': [{
            'source_ref': exp['source_ref'],
            'evidence_complete_rank': rank,
            'evidence_covered': coverage,
            'fail_category': fail_category,
        }],
        'outcome': outcome,
        'fail_reason': fail_reason,
    }


def _score_multi_source(question: dict, retrieved: list[RetrievedChunk], source_ref_map: dict[str, tuple[str, str]]) -> dict:
    expected = question['expected_sources']
    per_source = []
    ranks = []
    missing = []  # list of (source_ref, fail_category) for expected sources with rank is None
    for exp in expected:
        source_type, source_id = source_ref_map[exp['source_ref']]
        rank = _evidence_complete_rank(exp['evidence'], source_type, source_id, retrieved)
        coverage = _evidence_coverage(exp['evidence'], source_type, source_id, retrieved)
        fail_category = None if rank is not None else _missing_source_category(source_type, source_id, retrieved)
        per_source.append({
            'source_ref': exp['source_ref'],
            'evidence_complete_rank': rank,
            'evidence_covered': coverage,
            'fail_category': fail_category,
        })
        ranks.append(rank)
        if rank is None:
            missing.append(f"{exp['source_ref']} ({fail_category})")

    if not missing:
        outcome = 'pass'
        fail_reason = None
    elif len(missing) == len(expected):
        outcome = 'fail'
        fail_reason = f"none of the expected sources were satisfied: {', '.join(missing)}"
    else:
        outcome = 'partial'
        fail_reason = f"expected source(s) not satisfied: {', '.join(missing)}"

    expected_keys = {source_ref_map[exp['source_ref']] for exp in expected}
    first_rel_rank = _first_relevant_rank(expected_keys, retrieved)

    recall_precision = {}
    for k in (3, 5, 10):
        n_hit = sum(1 for r in ranks if r is not None and r <= k)
        recall_precision[f'recall@{k}'] = n_hit / len(expected)
        # Full-Coverage@k: 1.0 iff EVERY expected source is evidence-complete
        # within the top k (n_hit == len(expected)), else 0.0 -- an all-or-
        # nothing companion to Recall@k's partial-credit average, since a
        # partially-covered multi-source answer is still an incomplete answer.
        recall_precision[f'full_coverage@{k}'] = 1.0 if n_hit == len(expected) else 0.0
        if k in (3, 5):
            recall_precision[f'precision@{k}'] = _evidence_relevant_count_at_k(expected, source_ref_map, retrieved, k) / k
            recall_precision[f'source_precision@{k}'] = _source_match_count_at_k(expected_keys, retrieved, k) / k

    return {
        'per_expected_source': per_source,
        'outcome': outcome,
        'fail_reason': fail_reason,
        'first_relevant_rank': first_rel_rank,
        **recall_precision,
    }


def _score_no_answer(question: dict, retrieved: list[RetrievedChunk], source_ref_map_reverse: dict[tuple[str, str], str]) -> dict:
    return {
        'top_retrieved': [_chunk_to_dict(rank, chunk, source_ref_map_reverse) for rank, chunk in enumerate(retrieved[:5], start=1)],
        'agent_abstained': None,
        'outcome': None,
        'fail_reason': None,
    }


def _run_questions(project: Project, questions: list[dict], source_ref_map: dict[str, tuple[str, str]], top_k: int) -> list[dict]:
    source_ref_map_reverse = {v: k for k, v in source_ref_map.items()}
    per_question = []

    for q in questions:
        retrieved = retrieve_chunks(project.id, q['question'], top_k=top_k, min_similarity=None)
        record: dict[str, Any] = {
            'id': q['id'],
            'question_type': q['question_type'],
            'question': q['question'],
            'tags': q.get('tags', []),
            'expected_sources': q.get('expected_sources', []),
            'retrieved_top5': [
                _chunk_to_dict(rank, chunk, source_ref_map_reverse)
                for rank, chunk in enumerate(retrieved[:5], start=1)
            ],
        }

        if q['question_type'] == 'single-source':
            record.update(_score_single_source(q, retrieved, source_ref_map))
        elif q['question_type'] == 'multi-source':
            record.update(_score_multi_source(q, retrieved, source_ref_map))
        elif q['question_type'] == 'no-answer':
            record.update(_score_no_answer(q, retrieved, source_ref_map_reverse))
        else:
            raise CommandError(f"Unknown question_type {q['question_type']!r} for {q['id']}")

        per_question.append(record)

    return per_question


def _aggregate_metrics(per_question: list[dict]) -> dict:
    single = [q for q in per_question if q['question_type'] == 'single-source']
    multi = [q for q in per_question if q['question_type'] == 'multi-source']
    no_answer = [q for q in per_question if q['question_type'] == 'no-answer']

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    single_ranks = [q['evidence_complete_rank'] for q in single]
    single_metrics = {
        'n': len(single),
        'hit@1': _mean([1.0 if r is not None and r <= 1 else 0.0 for r in single_ranks]),
        'hit@3': _mean([1.0 if r is not None and r <= 3 else 0.0 for r in single_ranks]),
        'hit@5': _mean([1.0 if r is not None and r <= 5 else 0.0 for r in single_ranks]),
        'mrr': _mean([1.0 / r if r is not None else 0.0 for r in single_ranks]),
    }

    multi_metrics = {
        'n': len(multi),
        'recall@3': _mean([q['recall@3'] for q in multi]),
        'recall@5': _mean([q['recall@5'] for q in multi]),
        'recall@10': _mean([q['recall@10'] for q in multi]),
        'full_coverage@3': _mean([q['full_coverage@3'] for q in multi]),
        'full_coverage@5': _mean([q['full_coverage@5'] for q in multi]),
        'full_coverage@10': _mean([q['full_coverage@10'] for q in multi]),
        'precision@3': _mean([q['precision@3'] for q in multi]),
        'precision@5': _mean([q['precision@5'] for q in multi]),
        'source_precision@3_diagnostic': _mean([q['source_precision@3'] for q in multi]),
        'source_precision@5_diagnostic': _mean([q['source_precision@5'] for q in multi]),
        'aux_mrr_first_relevant': _mean([1.0 / q['first_relevant_rank'] if q['first_relevant_rank'] else 0.0 for q in multi]),
    }

    return {
        'single_source': single_metrics,
        'multi_source': multi_metrics,
        'no_answer': {'n': len(no_answer), 'note': 'excluded from Hit@K/MRR/Recall/Precision by design; see per-question diagnostics'},
    }


# ---------------------------------------------------------------------------
# Tenant / project setup
# ---------------------------------------------------------------------------

def _get_or_create_eval_tenant() -> tuple[Organization, Any]:
    org, _ = Organization.objects.get_or_create(
        name=EVAL_ORG_NAME,
        defaults={'email_domain': EVAL_ORG_EMAIL_DOMAIN},
    )
    user = User.objects.filter(email=EVAL_USER_EMAIL).first()
    if user is None:
        user = User.objects.create_user(
            username=EVAL_USER_USERNAME,
            email=EVAL_USER_EMAIL,
            password=get_random_string(32),
            organization=org,
        )
    return org, user


def _get_or_create_eval_project(org: Organization, user) -> Project:
    project, _ = Project.objects.get_or_create(
        organization=org,
        name=EVAL_PROJECT_NAME,
        defaults={'owner': user},
    )
    return project


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Seed the MED-264 RAG eval fixture into a dedicated eval tenant, run the "
        "real indexing + retrieval pipeline (real Gemini embeddings, real pgvector), "
        "and report evidence-aware retrieval metrics by question type."
    )

    def add_arguments(self, parser):
        parser.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE, help='Path to the eval fixture JSON.')
        parser.add_argument('--output', type=Path, default=None, help='Path to write the JSON results artifact.')
        parser.add_argument('--top-k', type=int, default=10, help='top_k passed to retrieve_chunks() for this eval run only.')

    def handle(self, *args, **options):
        provider = settings.RAG_EMBEDDING_PROVIDER
        if provider == 'gemini':
            embedding_model = settings.RAG_EMBEDDING_MODEL
        elif provider == 'local':
            embedding_model = settings.RAG_LOCAL_EMBEDDING_MODEL
        else:
            raise CommandError(
                f"Unknown RAG_EMBEDDING_PROVIDER {provider!r}; expected 'gemini' or 'local'."
            )

        if provider == 'gemini' and not settings.GEMINI_API_KEY:
            raise CommandError(
                "GEMINI_API_KEY is not configured. This command makes real Gemini "
                "embedding calls (document + query) and cannot run without it. "
                "Set RAG_EMBEDDING_PROVIDER=local to use the temporary local "
                "embedding provider instead (eval/local-dev only)."
            )

        fixture_path: Path = options['fixture']
        top_k: int = options['top_k']

        data = _load_fixture(fixture_path)
        _validate_fixture_shape(data, fixture_path)

        org, user = _get_or_create_eval_tenant()
        eval_schema = slug_to_schema_name(org.slug)
        self.stdout.write(f"Eval tenant: organization={org.name!r} schema={eval_schema!r}")

        with tenant_schema_context(eval_schema):
            project = _get_or_create_eval_project(org, user)
            self.stdout.write(f"Eval project id={project.id} name={project.name!r}")

            source_ref_map, index_outcomes = _seed_and_index(project, user, data['sources'])
            _validate_question_refs(data['questions'], source_ref_map)
            per_question = _run_questions(project, data['questions'], source_ref_map, top_k)

        metrics = _aggregate_metrics(per_question)

        report = {
            'run_metadata': {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'fixture_path': str(fixture_path),
                'fixture_version': data.get('dataset_version'),
                'chunk_size': settings.RAG_CHUNK_SIZE,
                'chunk_overlap': settings.RAG_CHUNK_OVERLAP,
                'embedding_provider': provider,
                'embedding_model': embedding_model,
                'embedding_dimensions': settings.RAG_EMBEDDING_DIMENSIONS,
                'min_similarity': None,
                'top_k': top_k,
                'eval_org_schema': eval_schema,
                'eval_project_id': project.id,
            },
            'index_outcomes': index_outcomes,
            'metrics': metrics,
            'per_question': per_question,
        }
        if 'partial_baseline' in data:
            # Carried through verbatim from the fixture (e.g. the no-retro
            # variant) so the JSON artifact self-documents that this run is
            # partial/provisional and why -- never inferred or reworded here.
            report['run_metadata']['partial_baseline'] = data['partial_baseline']

        output_path = self._write_report(report, options['output'])
        self._print_summary(report, output_path)

    def _write_report(self, report: dict, output_option: Path | None) -> Path:
        if output_option is not None:
            output_path = output_option
        else:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            output_path = DEFAULT_OUTPUT_DIR / f'{stamp}.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, sort_keys=False)
        return output_path

    def _print_summary(self, report: dict, output_path: Path) -> None:
        partial_baseline = report['run_metadata'].get('partial_baseline')
        if partial_baseline:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "PARTIAL/PROVISIONAL BASELINE -- "
                f"{partial_baseline.get('reason', 'see run_metadata.partial_baseline for details')}"
            ))
            excluded = partial_baseline.get('excluded_source_types')
            if excluded:
                self.stdout.write(self.style.WARNING(f"Excluded source types: {', '.join(excluded)}"))

        outcome_counts: dict[str, int] = {}
        for o in report['index_outcomes']:
            outcome_counts[o['outcome']] = outcome_counts.get(o['outcome'], 0) + 1
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Indexing outcomes"))
        for outcome, count in sorted(outcome_counts.items()):
            self.stdout.write(f"  {outcome}: {count}")

        m = report['metrics']
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Single-source (n={m['single_source']['n']})"))
        self.stdout.write(f"  Hit@1={m['single_source']['hit@1']:.3f}  Hit@3={m['single_source']['hit@3']:.3f}  "
                           f"Hit@5={m['single_source']['hit@5']:.3f}  MRR={m['single_source']['mrr']:.3f}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Multi-source (n={m['multi_source']['n']})"))
        self.stdout.write(f"  Recall@3={m['multi_source']['recall@3']:.3f}  Recall@5={m['multi_source']['recall@5']:.3f}"
                           f"  Recall@10={m['multi_source']['recall@10']:.3f}")
        self.stdout.write(f"  Full-Coverage@3={m['multi_source']['full_coverage@3']:.3f}  "
                           f"Full-Coverage@5={m['multi_source']['full_coverage@5']:.3f}  "
                           f"Full-Coverage@10={m['multi_source']['full_coverage@10']:.3f}")
        self.stdout.write(f"  Precision@3={m['multi_source']['precision@3']:.3f}  Precision@5={m['multi_source']['precision@5']:.3f}"
                           f"  (source_precision@3={m['multi_source']['source_precision@3_diagnostic']:.3f}, "
                           f"@5={m['multi_source']['source_precision@5_diagnostic']:.3f})")
        self.stdout.write(f"  aux MRR (first relevant)={m['multi_source']['aux_mrr_first_relevant']:.3f}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"No-answer (n={m['no_answer']['n']})"))
        self.stdout.write(f"  {m['no_answer']['note']}")

        failing = [q for q in report['per_question'] if q.get('outcome') in ('fail', 'partial')]
        if failing:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"{len(failing)} question(s) not fully passing:"))
            for q in failing:
                self.stdout.write(f"  [{q['outcome']}] {q['id']}: {q.get('fail_reason')}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Results written to {output_path}"))
