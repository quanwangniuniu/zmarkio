"""
Management command: run_project_rag_followup_eval.

Arms:
  A -- retrieve with the raw follow-up question
  B -- retrieve with the human expected_rewrite (upper-bound reference only)
  C -- retrieve with rewrite_project_question() output (real rewrite path)

Default runs A,B,C. Use --arms A,B to skip the real rewrite call. When C is
included, failures/timeouts that fall back to the original question still
count toward C's score. Reports must state that C is what proves rewrite
helps relative to A; B is only an ideal-completion ceiling.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from agent.models import AgentMessage, AgentSession
from agent.query_rewrite import rewrite_project_question_result
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from rag.management.commands import run_project_rag_eval as main_eval
from rag.retrieval import retrieve_chunks

_COMMAND_DIR = Path(__file__).resolve().parent
_RAG_APP_DIR = _COMMAND_DIR.parents[1]
_FIXTURES_DIR = _RAG_APP_DIR / 'tests' / 'fixtures'
DEFAULT_FOLLOWUP_FIXTURE = _FIXTURES_DIR / 'project_rag_followup_eval_dataset.json'
DEFAULT_OUTPUT_DIR = _RAG_APP_DIR / 'eval_results'

ALLOWED_CASE_KINDS = frozenset({
    'anaphoric',
    'already_complete',
    'topic_shift',
    'underspecified',
})
ALLOWED_QUESTION_TYPES = frozenset({'single-source', 'multi-source', 'no-answer'})
ALLOWED_HISTORY_ROLES = frozenset({'user', 'assistant'})
ALLOWED_ARMS = ('A', 'B', 'C')

PHASE_NOTE_AB = (
    'This run executes arms A and B only: raw short follow-up vs human '
    'expected_rewrite. It does not call rewrite_project_question and must '
    'not be reported as proof that query rewrite works.'
)
PHASE_NOTE_WITH_C = (
    'This run includes arm C (real rewrite_project_question). Primary claim: '
    'how C compares to A. Arm B is an upper-bound reference only (ideal human '
    'completion) and must not be reported as proof that rewrite works. '
    'Rewrite failures/timeouts that fall back to the original question still '
    'count toward C.'
)

_OUTCOME_SCORE = {'fail': 0, 'partial': 1, 'pass': 2}

# Back-compat export for existing tests that imported PHASE_NOTE.
PHASE_NOTE = PHASE_NOTE_AB


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise CommandError(f"Fixture not found: {path}")
    try:
        with path.open(encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise CommandError(f"Fixture at {path} is not valid JSON: {exc}") from exc


def _parse_arms(raw: str) -> tuple[str, ...]:
    arms = tuple(part.strip().upper() for part in raw.split(',') if part.strip())
    if not arms:
        raise CommandError('--arms must list at least one of A, B, C.')
    unknown = [arm for arm in arms if arm not in ALLOWED_ARMS]
    if unknown:
        raise CommandError(f"Unknown arm(s) {unknown}; expected subset of A,B,C.")
    # Preserve canonical order A,B,C even if the user reorders the flag.
    return tuple(arm for arm in ALLOWED_ARMS if arm in arms)


def _validate_followup_fixture(data: dict, path: Path) -> None:
    questions = data.get('questions')
    if not isinstance(questions, list) or not questions:
        raise CommandError(f"Follow-up fixture {path} must declare a non-empty questions list.")

    corpus = data.get('source_corpus') or {}
    if not corpus.get('fixture'):
        raise CommandError(f"Follow-up fixture {path} must declare source_corpus.fixture.")

    case_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    ids: set[str] = set()

    for q in questions:
        qid = q.get('id')
        if not qid:
            raise CommandError(f"Follow-up fixture {path} has a question without id.")
        if qid in ids:
            raise CommandError(f"Follow-up fixture {path} has duplicate question id {qid!r}.")
        ids.add(qid)

        case_kind = q.get('case_kind')
        if case_kind not in ALLOWED_CASE_KINDS:
            raise CommandError(f"{qid}: invalid case_kind {case_kind!r}.")
        case_counts[case_kind] = case_counts.get(case_kind, 0) + 1

        qtype = q.get('question_type')
        if qtype not in ALLOWED_QUESTION_TYPES:
            raise CommandError(f"{qid}: invalid question_type {qtype!r}.")
        type_counts[qtype] = type_counts.get(qtype, 0) + 1

        history = q.get('history')
        if not isinstance(history, list) or not history:
            raise CommandError(f"{qid}: history must be a non-empty list of prior turns.")
        for turn in history:
            if turn.get('role') not in ALLOWED_HISTORY_ROLES:
                raise CommandError(f"{qid}: history role must be user or assistant.")
            if not isinstance(turn.get('content'), str) or not turn['content'].strip():
                raise CommandError(f"{qid}: history content must be a non-empty string.")
        history_turns = _history_turn_count(history)
        if history_turns < 1:
            raise CommandError(
                f"{qid}: history must contain at least one completed user+assistant turn."
            )
        tags = q.get('tags') or []
        if not isinstance(tags, list):
            raise CommandError(f"{qid}: tags must be a list when present.")
        if 'multi-turn' in tags and history_turns < 2:
            raise CommandError(
                f"{qid}: tag multi-turn requires history with at least 2 completed turns "
                f"(found {history_turns})."
            )
        default_window = settings.RAG_QUERY_REWRITE_HISTORY_TURNS
        if 'beyond-default-window' in tags and history_turns <= default_window:
            raise CommandError(
                f"{qid}: tag beyond-default-window requires history_turn_count > "
                f"RAG_QUERY_REWRITE_HISTORY_TURNS ({default_window}); found {history_turns}."
            )

        if not isinstance(q.get('question'), str) or not q['question'].strip():
            raise CommandError(f"{qid}: question must be a non-empty string.")
        if not isinstance(q.get('expected_rewrite'), str) or not q['expected_rewrite'].strip():
            raise CommandError(f"{qid}: expected_rewrite must be a non-empty string.")

        expected_sources = q.get('expected_sources')
        if not isinstance(expected_sources, list):
            raise CommandError(f"{qid}: expected_sources must be a list.")
        if qtype == 'no-answer' and expected_sources:
            raise CommandError(f"{qid}: no-answer questions must have empty expected_sources.")
        if qtype != 'no-answer' and not expected_sources:
            raise CommandError(f"{qid}: {qtype} questions must declare expected_sources.")

    expected_case = data.get('expected_case_kind_counts')
    if expected_case is not None:
        normalized_case = {kind: case_counts.get(kind, 0) for kind in ALLOWED_CASE_KINDS}
        expected_case_full = {kind: int(expected_case.get(kind, 0)) for kind in ALLOWED_CASE_KINDS}
        if normalized_case != expected_case_full:
            raise CommandError(
                f"Follow-up fixture {path} case_kind counts {normalized_case} do not match "
                f"expected_case_kind_counts {expected_case_full}."
            )
    expected_types = data.get('expected_question_type_counts')
    if expected_types is not None:
        normalized_types = {qtype: type_counts.get(qtype, 0) for qtype in ALLOWED_QUESTION_TYPES}
        expected_types_full = {
            qtype: int(expected_types.get(qtype, 0)) for qtype in ALLOWED_QUESTION_TYPES
        }
        if normalized_types != expected_types_full:
            raise CommandError(
                f"Follow-up fixture {path} question_type counts {normalized_types} do not match "
                f"expected_question_type_counts {expected_types_full}."
            )


def _resolve_source_corpus_path(followup_data: dict, followup_path: Path) -> Path:
    corpus_name = followup_data['source_corpus']['fixture']
    candidate = followup_path.parent / corpus_name
    if candidate.exists():
        return candidate
    fallback = _FIXTURES_DIR / corpus_name
    if fallback.exists():
        return fallback
    raise CommandError(
        f"Source corpus fixture {corpus_name!r} not found next to {followup_path} "
        f"or under {_FIXTURES_DIR}."
    )


def _score_arm(
    question: dict,
    query: str,
    project_id: int,
    source_ref_map: dict[str, tuple[str, str]],
    top_k: int,
) -> dict[str, Any]:
    retrieved = retrieve_chunks(project_id, query, top_k=top_k, min_similarity=None)
    source_ref_map_reverse = {v: k for k, v in source_ref_map.items()}
    record: dict[str, Any] = {
        'query': query,
        'retrieved_top5': [
            main_eval._chunk_to_dict(rank, chunk, source_ref_map_reverse)
            for rank, chunk in enumerate(retrieved[:5], start=1)
        ],
    }
    qtype = question['question_type']
    if qtype == 'single-source':
        record.update(main_eval._score_single_source(question, retrieved, source_ref_map))
    elif qtype == 'multi-source':
        record.update(main_eval._score_multi_source(question, retrieved, source_ref_map))
    elif qtype == 'no-answer':
        record.update(main_eval._score_no_answer(question, retrieved, source_ref_map_reverse))
    else:
        raise CommandError(f"Unknown question_type {qtype!r} for {question['id']}")
    return record


def _compare_arms(arm_left: dict, arm_right: dict) -> str:
    """Whether right looks better / worse / same than left for ranking outcomes."""
    left_out, right_out = arm_left.get('outcome'), arm_right.get('outcome')
    if left_out is None and right_out is None:
        return 'n/a'
    left_score = _OUTCOME_SCORE.get(left_out)
    right_score = _OUTCOME_SCORE.get(right_out)
    if left_score is None or right_score is None:
        return 'n/a'
    if right_score > left_score:
        return 'improve'
    if right_score < left_score:
        return 'regress'
    left_rank = arm_left.get('evidence_complete_rank')
    right_rank = arm_right.get('evidence_complete_rank')
    if left_rank is not None and right_rank is not None:
        if right_rank < left_rank:
            return 'improve'
        if right_rank > left_rank:
            return 'regress'
    return 'same'


# Back-compat name used by unit tests.
_compare_a_to_b = _compare_arms


def _history_turn_count(history: list[dict]) -> int:
    """Count completed user→assistant pairs (oldest-first fixture shape)."""
    turns = 0
    i = 0
    while i + 1 < len(history):
        if history[i].get('role') == 'user' and history[i + 1].get('role') == 'assistant':
            turns += 1
            i += 2
        else:
            i += 1
    return turns


def _seed_rewrite_session(user, project, question_row: dict) -> AgentSession:
    """Persist history + current question the same way production chat does."""
    session = AgentSession.objects.create(user=user, project=project)
    for turn in question_row['history']:
        AgentMessage.objects.create(
            session=session,
            role=turn['role'],
            content=turn['content'],
            message_type='text',
        )
    AgentMessage.objects.create(
        session=session,
        role='user',
        content=question_row['question'],
        message_type='text',
    )
    return session


def _run_arm_c(user, project, question_row: dict) -> dict[str, Any]:
    session = _seed_rewrite_session(user, project, question_row)
    started = time.perf_counter()
    result = rewrite_project_question_result(session, project, question_row['question'])
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        'rewritten_question': result.question,
        'fell_back': result.fell_back,
        'reason': result.reason,
        'latency_ms': latency_ms,
        'session_id': str(session.id),
    }


def _run_followup_questions(
    project,
    user,
    questions: list[dict],
    source_ref_map: dict[str, tuple[str, str]],
    top_k: int,
    arms: tuple[str, ...],
) -> list[dict]:
    per_question = []
    for q in questions:
        arms_out: dict[str, Any] = {}
        if 'A' in arms:
            arms_out['A'] = _score_arm(q, q['question'], project.id, source_ref_map, top_k)
        if 'B' in arms:
            arms_out['B'] = _score_arm(q, q['expected_rewrite'], project.id, source_ref_map, top_k)
        if 'C' in arms:
            rewrite_meta = _run_arm_c(user, project, q)
            arm_c = _score_arm(
                q, rewrite_meta['rewritten_question'], project.id, source_ref_map, top_k,
            )
            arm_c.update(rewrite_meta)
            arms_out['C'] = arm_c

        comparison: dict[str, str] = {}
        if 'A' in arms_out and 'B' in arms_out:
            comparison['A_to_B'] = _compare_arms(arms_out['A'], arms_out['B'])
        if 'A' in arms_out and 'C' in arms_out:
            comparison['A_to_C'] = _compare_arms(arms_out['A'], arms_out['C'])
        if 'B' in arms_out and 'C' in arms_out:
            comparison['B_to_C'] = _compare_arms(arms_out['B'], arms_out['C'])

        tags = q.get('tags', [])
        history_turn_count = _history_turn_count(q['history'])
        per_question.append({
            'id': q['id'],
            'case_kind': q['case_kind'],
            'question_type': q['question_type'],
            'include_in_followup_metrics': q.get('include_in_followup_metrics', True),
            'history': q['history'],
            'history_turn_count': history_turn_count,
            'multi_turn': 'multi-turn' in tags,
            'beyond_default_window': 'beyond-default-window' in tags,
            'question': q['question'],
            'expected_rewrite': q['expected_rewrite'],
            'expected_sources': q.get('expected_sources', []),
            'tags': tags,
            'notes': q.get('notes'),
            'derived_from': q.get('derived_from'),
            'arms': arms_out,
            'comparison': comparison,
        })
    return per_question


def _arm_records_for_aggregate(per_question: list[dict], arm: str) -> list[dict]:
    """Shape arm rows like main-eval per_question so _aggregate_metrics can reuse."""
    rows = []
    for q in per_question:
        if not q.get('include_in_followup_metrics', True):
            continue
        if arm not in q['arms']:
            continue
        arm_row = q['arms'][arm]
        rows.append({
            'id': q['id'],
            'question_type': q['question_type'],
            'outcome': arm_row.get('outcome'),
            'evidence_complete_rank': arm_row.get('evidence_complete_rank'),
            'recall@3': arm_row.get('recall@3', 0.0),
            'recall@5': arm_row.get('recall@5', 0.0),
            'recall@10': arm_row.get('recall@10', 0.0),
            'full_coverage@3': arm_row.get('full_coverage@3', 0.0),
            'full_coverage@5': arm_row.get('full_coverage@5', 0.0),
            'full_coverage@10': arm_row.get('full_coverage@10', 0.0),
            'precision@3': arm_row.get('precision@3', 0.0),
            'precision@5': arm_row.get('precision@5', 0.0),
            'source_precision@3': arm_row.get('source_precision@3', 0.0),
            'source_precision@5': arm_row.get('source_precision@5', 0.0),
            'first_relevant_rank': arm_row.get('first_relevant_rank'),
        })
    return rows


def _summarize_comparisons(per_question: list[dict], key: str) -> dict[str, int]:
    counts = {'improve': 0, 'regress': 0, 'same': 0, 'n/a': 0}
    for q in per_question:
        if not q.get('include_in_followup_metrics', True):
            continue
        label = q['comparison'].get(key)
        if label is None:
            continue
        counts[label] = counts.get(label, 0) + 1
    return counts


def _summarize_rewrite(per_question: list[dict]) -> dict[str, Any]:
    latencies: list[float] = []
    fallbacks = 0
    reasons: dict[str, int] = {}
    n = 0
    for q in per_question:
        arm_c = q['arms'].get('C')
        if not arm_c:
            continue
        n += 1
        latencies.append(float(arm_c['latency_ms']))
        if arm_c.get('fell_back'):
            fallbacks += 1
        reason = arm_c.get('reason') or 'unknown'
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        'n': n,
        'fallback_count': fallbacks,
        'fallback_rate': (fallbacks / n) if n else 0.0,
        'latency_ms_mean': (sum(latencies) / n) if n else 0.0,
        'latency_ms_max': max(latencies) if latencies else 0.0,
        'reasons': reasons,
    }


class Command(BaseCommand):
    help = (
        "Seed the MED-264 source corpus used by the follow-up fixture, then score "
        "each follow-up question under arms A (raw), B (human expected_rewrite), "
        "and C (rewrite_project_question). Default arms=A,B,C."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fixture',
            type=Path,
            default=DEFAULT_FOLLOWUP_FIXTURE,
            help='Path to the follow-up eval fixture JSON.',
        )
        parser.add_argument(
            '--output',
            type=Path,
            default=None,
            help='Path to write the JSON results artifact.',
        )
        parser.add_argument(
            '--top-k',
            type=int,
            default=10,
            help='top_k passed to retrieve_chunks() for this eval run only.',
        )
        parser.add_argument(
            '--arms',
            default='A,B,C',
            help='Comma-separated arms to run (subset of A,B,C). Default A,B,C.',
        )

    def handle(self, *args, **options):
        arms = _parse_arms(options['arms'])
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
                "GEMINI_API_KEY is not configured. Set RAG_EMBEDDING_PROVIDER=local "
                "to use the temporary local embedding provider instead "
                "(eval/local-dev only)."
            )
        if 'C' in arms and not settings.GEMINI_API_KEY:
            raise CommandError(
                "Arm C calls rewrite_project_question and requires GEMINI_API_KEY. "
                "Pass --arms A,B to skip real rewrite, or set GEMINI_API_KEY."
            )

        followup_path: Path = options['fixture']
        top_k: int = options['top_k']
        followup_data = _load_json(followup_path)
        _validate_followup_fixture(followup_data, followup_path)

        corpus_path = _resolve_source_corpus_path(followup_data, followup_path)
        corpus_data = _load_json(corpus_path)
        main_eval._validate_fixture_shape(corpus_data, corpus_path)

        phase_note = PHASE_NOTE_WITH_C if 'C' in arms else PHASE_NOTE_AB
        phase = 'A_B_C' if 'C' in arms else 'A_B_only'

        org, user = main_eval._get_or_create_eval_tenant()
        eval_schema = slug_to_schema_name(org.slug)
        self.stdout.write(f"Eval tenant: organization={org.name!r} schema={eval_schema!r}")
        self.stdout.write(self.style.WARNING(phase_note))

        with tenant_schema_context(eval_schema):
            project = main_eval._get_or_create_eval_project(org, user)
            self.stdout.write(f"Eval project id={project.id} name={project.name!r}")
            source_ref_map, index_outcomes = main_eval._seed_and_index(
                project, user, corpus_data['sources'],
            )
            main_eval._validate_question_refs(followup_data['questions'], source_ref_map)
            per_question = _run_followup_questions(
                project, user, followup_data['questions'], source_ref_map, top_k, arms,
            )

        metrics_by_arm = {
            arm: main_eval._aggregate_metrics(_arm_records_for_aggregate(per_question, arm))
            for arm in arms
        }
        report: dict[str, Any] = {
            'run_metadata': {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'fixture_path': str(followup_path),
                'fixture_version': followup_data.get('dataset_version'),
                'source_corpus_path': str(corpus_path),
                'source_corpus_version': corpus_data.get('dataset_version'),
                'chunk_size': settings.RAG_CHUNK_SIZE,
                'chunk_overlap': settings.RAG_CHUNK_OVERLAP,
                'embedding_provider': provider,
                'embedding_model': embedding_model,
                'embedding_dimensions': settings.RAG_EMBEDDING_DIMENSIONS,
                'min_similarity': None,
                'top_k': top_k,
                'eval_org_schema': eval_schema,
                'eval_project_id': project.id,
                'arms_executed': list(arms),
                'phase': phase,
                'phase_note': phase_note,
            },
            'index_outcomes': index_outcomes,
            'metrics_by_arm': metrics_by_arm,
            'per_question': per_question,
        }
        if 'A' in arms and 'B' in arms:
            report['comparison_A_to_B'] = _summarize_comparisons(per_question, 'A_to_B')
        if 'A' in arms and 'C' in arms:
            report['comparison_A_to_C'] = _summarize_comparisons(per_question, 'A_to_C')
        if 'B' in arms and 'C' in arms:
            report['comparison_B_to_C'] = _summarize_comparisons(per_question, 'B_to_C')
        if 'C' in arms:
            report['rewrite_summary'] = _summarize_rewrite(per_question)

        output_path = self._write_report(report, options['output'])
        self._print_summary(report, output_path)

    def _write_report(self, report: dict, output_option: Path | None) -> Path:
        if output_option is not None:
            output_path = output_option
        else:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            output_path = DEFAULT_OUTPUT_DIR / f'followup_{stamp}.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, sort_keys=False)
        return output_path

    def _print_summary(self, report: dict, output_path: Path) -> None:
        meta = report['run_metadata']
        arms = meta['arms_executed']
        arm_labels = {
            'A': 'A raw follow-up',
            'B': 'B human expected_rewrite (upper bound only)',
            'C': 'C rewrite_project_question',
        }
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Follow-up eval phase"))
        self.stdout.write(self.style.WARNING(meta['phase_note']))
        self.stdout.write(f"  arms_executed={arms}")

        outcome_counts: dict[str, int] = {}
        for o in report['index_outcomes']:
            outcome_counts[o['outcome']] = outcome_counts.get(o['outcome'], 0) + 1
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Indexing outcomes"))
        for outcome, count in sorted(outcome_counts.items()):
            self.stdout.write(f"  {outcome}: {count}")

        for arm in arms:
            m = report['metrics_by_arm'][arm]
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(f"Arm {arm_labels[arm]}"))
            self.stdout.write(
                f"  Single-source n={m['single_source']['n']}  "
                f"Hit@1={m['single_source']['hit@1']:.3f}  "
                f"Hit@3={m['single_source']['hit@3']:.3f}  "
                f"MRR={m['single_source']['mrr']:.3f}"
            )
            self.stdout.write(
                f"  Multi-source n={m['multi_source']['n']}  "
                f"Recall@5={m['multi_source']['recall@5']:.3f}  "
                f"Full-Coverage@5={m['multi_source']['full_coverage@5']:.3f}"
            )
            self.stdout.write(f"  No-answer n={m['no_answer']['n']} (excluded from ranking metrics)")

        if 'rewrite_summary' in report:
            rs = report['rewrite_summary']
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("Rewrite (arm C)"))
            self.stdout.write(
                f"  n={rs['n']}  fallbacks={rs['fallback_count']}  "
                f"fallback_rate={rs['fallback_rate']:.3f}  "
                f"latency_ms_mean={rs['latency_ms_mean']:.1f}  "
                f"latency_ms_max={rs['latency_ms_max']:.1f}"
            )
            self.stdout.write(f"  reasons={rs['reasons']}")

        multi_turn_rows = [q for q in report['per_question'] if q.get('multi_turn')]
        if multi_turn_rows:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"Multi-turn rewrite subset (n={len(multi_turn_rows)})"
            ))
            self.stdout.write(
                "  History has 2+ completed turns; check recent-referent binding "
                "and no graft after topic shift."
            )
            for q in multi_turn_rows:
                a_to_c = q.get('comparison', {}).get('A_to_C', 'n/a')
                arm_c = q['arms'].get('C') or {}
                rewritten = arm_c.get('rewritten_question') or arm_c.get('query') or ''
                self.stdout.write(
                    f"  [{a_to_c}] {q['id']} ({q['case_kind']}, "
                    f"history_turns={q.get('history_turn_count')}): "
                    f"C={rewritten!r}"
                )

        beyond_rows = [q for q in report['per_question'] if q.get('beyond_default_window')]
        if beyond_rows:
            window = settings.RAG_QUERY_REWRITE_HISTORY_TURNS
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"Beyond-default-window subset (n={len(beyond_rows)}, "
                f"RAG_QUERY_REWRITE_HISTORY_TURNS={window})"
            ))
            self.stdout.write(
                "  Oldest turn(s) are dropped from the rewrite prompt; "
                "C must not invent facts only present outside the window."
            )
            for q in beyond_rows:
                a_to_c = q.get('comparison', {}).get('A_to_C', 'n/a')
                arm_c = q['arms'].get('C') or {}
                rewritten = arm_c.get('rewritten_question') or arm_c.get('query') or ''
                self.stdout.write(
                    f"  [{a_to_c}] {q['id']} ({q['case_kind']}, "
                    f"history_turns={q.get('history_turn_count')}): "
                    f"C={rewritten!r}"
                )

        multi_source_rows = [
            q for q in report['per_question'] if q.get('question_type') == 'multi-source'
        ]
        if multi_source_rows and 'C' in report['run_metadata']['arms_executed']:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"Multi-source follow-up subset (n={len(multi_source_rows)})"
            ))
            for q in multi_source_rows:
                a_to_c = q.get('comparison', {}).get('A_to_C', 'n/a')
                arm_c = q['arms'].get('C') or {}
                rewritten = arm_c.get('rewritten_question') or arm_c.get('query') or ''
                recall = arm_c.get('recall@5')
                self.stdout.write(
                    f"  [{a_to_c}] {q['id']} recall@5={recall} "
                    f"C={rewritten!r}"
                )

        for key, title in (
            ('comparison_A_to_B', 'A → B (ideal completion vs raw; reference only)'),
            ('comparison_A_to_C', 'A → C (real rewrite vs raw; primary)'),
            ('comparison_B_to_C', 'B → C (real rewrite vs ideal completion)'),
        ):
            if key not in report:
                continue
            cmp_counts = report[key]
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(title))
            self.stdout.write(
                f"  improve={cmp_counts['improve']}  regress={cmp_counts['regress']}  "
                f"same={cmp_counts['same']}  n/a={cmp_counts['n/a']}"
            )
            left, right = key.replace('comparison_', '').split('_to_')
            for q in report['per_question']:
                label = q['comparison'].get(f'{left}_to_{right}')
                if label is None:
                    continue
                left_out = q['arms'][left].get('outcome')
                right_out = q['arms'][right].get('outcome')
                extra = ''
                if right == 'C':
                    extra = (
                        f" rewrite_fell_back={q['arms']['C'].get('fell_back')} "
                        f"reason={q['arms']['C'].get('reason')} "
                        f"latency_ms={q['arms']['C'].get('latency_ms')}"
                    )
                self.stdout.write(
                    f"  [{label}] {q['id']} ({q['case_kind']}): "
                    f"{left}={left_out} {right}={right_out}{extra}"
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Results written to {output_path}"))
