# MED-264 RAG eval: fixture, seed SQL, and baselines

## Canonical fixture

`backend/rag/tests/fixtures/project_rag_eval_dataset.json` (`dataset_version`
`"2.1.0"`) is the single source of truth for the eval corpus. Composition, per
the fixture's own declared `expected_source_counts` / `expected_question_counts`
(also independently verified by counting every entry in the file):

- **Sources (58 total):** 25 meetings, 18 notion drafts, 15 retrospective tasks.
- **Questions (44 total):** 24 single-source, 15 multi-source, 5 no-answer.

A variant fixture, `project_rag_eval_dataset_no_retro.json`, excludes
retrospective sources and is used only for a provisional/partial baseline —
its results are intentionally **not** committed here (see below).

## Running the eval

```bash
RAG_EMBEDDING_PROVIDER=local python manage.py run_project_rag_eval
```

This seeds the fixture's 58 sources as real `Meeting`/`Draft`/
`RetrospectiveTask` rows in a dedicated `RAG Eval Harness` tenant, runs the
real chunking + embedding + pgvector retrieval pipeline, and scores each of
the 44 questions. `RAG_EMBEDDING_PROVIDER=local` uses the local
`BAAI/bge-base-en-v1.5` model (via fastembed) so the eval can run without a
`GEMINI_API_KEY`; omit it to use the `gemini` provider default instead.
Results are written to `backend/rag/eval_results/<UTC-timestamp>.json`
(gitignored — see "Tracked baseline" below) unless `--output` is passed
explicitly.

## Seed SQL generator

The eval fixture can also be materialized as a portable, idempotent SQL
script instead of being seeded via the Django ORM at eval time (used by the
MED-264 Playwright E2E fixture):

```bash
python manage.py generate_med264_seed_sql          # regenerate the seed SQL from the fixture
python manage.py generate_med264_seed_sql --check  # verify the committed SQL is still in sync; exits nonzero on drift
```

The generated file lives at `backend/rag/eval_seed/med264_seed.sql` and is
committed. It is a derived artifact — never hand-edit it; regenerate it from
the fixture instead. Apply it with:

```bash
psql <connection args> -v target_schema=org_rag_eval_harness -f backend/rag/eval_seed/med264_seed.sql
python manage.py ensure_med264_e2e_user --password <pw>   # or MED264_E2E_USER_PASSWORD env var
```

## Metrics produced by the harness

- **Single-source:** Hit@1, Hit@3, Hit@5, MRR.
- **Multi-source:** Recall@3/5/10, Full-Coverage@3/5/10, Precision@3/5 (plus
  a `source_precision@k` diagnostic and an auxiliary MRR on first relevant
  rank).
- **No-answer:** count only — excluded from the metrics above by design;
  visible per-question in the diagnostics (see below).

## Tracked baseline artifact

`backend/rag/eval_seed/baselines/med264_v2.1.0_local_bge_baseline.json` is
the **canonical local-BGE baseline** — a real `run_project_rag_eval` run
against fixture `dataset_version=2.1.0`, `RAG_EMBEDDING_PROVIDER=local`
(`BAAI/bge-base-en-v1.5`), `chunk_size=1500`, `chunk_overlap=200`, `top_k=10`.
It is committed so a reviewer can inspect real output without re-running the
harness. This is **not** a "fully-passing baseline" — the current run
contains three partial multi-source cases (`mq06`, `mq13`, `mq14`; see
below). Local eval runs (`backend/rag/eval_results/`) are gitignored by
default since they're timestamped/regenerated per run; this one file is the
deliberately-tracked exception, copied in under a different path rather than
un-ignoring the results directory.

The JSON contains, per question (`per_question`): `id`, `question_type`,
`question` text, `expected_sources`, and `retrieved_top5` — each retrieved
entry has `rank`, `source_ref`, `source_type`, `source_id`, `chunk_index`,
`similarity`, and a `content_snippet`. Each question also carries an
`outcome` (`pass`/`fail`/`partial`) and, when not a clean pass, a
`fail_reason` that names the unsatisfied source and classifies the failure
as `source_not_retrieved` (the document never appeared among retrieved
chunks) or `evidence_incomplete` (the document was retrieved but required
evidence spans weren't fully covered).

### Current local BGE aggregate baseline

Single-source (n=24): Hit@1=0.917, Hit@3=0.958, Hit@5=0.958, MRR=0.944

Multi-source (n=15): Recall@3=0.817, Recall@5=0.900, Recall@10=0.933,
Full-Coverage@3=0.600, Full-Coverage@5=0.733, Full-Coverage@10=0.800,
Precision@3=0.689, Precision@5=0.453

No-answer (n=5): excluded from the metrics above by design; see
per-question diagnostics in the JSON.

24 + 15 + 5 = 44 questions, matching the fixture's declared
`expected_question_counts` above.

### Known partial cases in this baseline

`mq06`, `mq13`, and `mq14` are `partial` multi-source questions in the
current baseline, each failing with `source_not_retrieved` on one expected
source (`meeting-010`, `meeting-014`, and `notion-018` respectively — all
valid refs within the fixture's 25 meetings / 18 notion drafts) — the
document was never surfaced among the retrieved chunks for that question.
These are pre-existing, categorized retrieval gaps in the current baseline,
not a flaky harness; see each question's `fail_reason` in the tracked JSON
for detail.
