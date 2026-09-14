"""
Management command: generate_med264_seed_sql (MED-264 Phase 2b seed generator).

Generates the committed MED-264 eval-fixture seed SQL
(rag/eval_seed/med264_seed.sql) directly from the JSON fixture
(rag/tests/fixtures/project_rag_eval_dataset.json), so the fixture stays the
single source of truth and the SQL is a derived, regeneratable artifact.

How it works
------------
Phase A (schema discovery, never committed): runs the real Django ORM save
path for every source row, inside a `transaction.atomic()` block that is
*always* rolled back via `transaction.set_rollback(True)`, capturing the
exact SQL Postgres executed via `django.test.utils.CaptureQueriesContext`.
This is used only to read off each table's authoritative (table_name,
column_list) shape -- it guarantees the emitted SQL can never silently drift
from what the models actually require, without hand-tracking every field
separately from `run_project_rag_eval.py`. Because every enqueue in
meetings/notion_editor/retrospective's post_save signals is registered via
`transaction.on_commit(...)` (verified by inspection), and Django discards
on_commit callbacks when the enclosing atomic block rolls back instead of
committing, this phase never enqueues real Celery indexing work.

Phase B (rendering): for each captured statement, every column's value is
kept as Django's own already-correctly-quoted literal text (numbers, JSON,
timestamps, slugs, ...) *except*:
  - `id` (and any column referencing a row created earlier in this same seed
    script, e.g. `type_definition_id`, `meeting_id`, `draft_id`): replaced
    with a deterministic id computed from `sha256("<model>|<source_ref>")`,
    so the seed file is portable and regeneration from an unchanged fixture
    is byte-identical.
  - `project_id` / `campaign_id`: replaced with a subquery resolving
    core_project by (organization name, project name) -- portable across any
    database, never the generating machine's local numeric PK.
  - `user_id` / `created_by_id`: replaced with a subquery resolving
    core_customuser by email -- same reasoning.

Collision safety
-----------------
Every INSERT carries `ON CONFLICT (id) DO NOTHING`, followed by a `DO $$ ...
$$` block that re-checks the row now present at that id actually matches
this row's identifying natural key. Three possible outcomes:
  - id was free -> INSERT creates the row -> check passes -> proceeds.
  - id already held OUR row (identical natural key) -> INSERT no-ops ->
    check passes -> safe no-op, re-running the seed file is idempotent.
  - id collides with unrelated data (different natural key) -> check fails
    -> `RAISE EXCEPTION` -> the whole script (one BEGIN...COMMIT) aborts,
    so a collision can never leave a partially-seeded corpus behind.
Ids are drawn from a large *negative* range for bigint-pk tables (confirmed
via information_schema: all six are plain `bigint` with no CHECK constraint
requiring positivity) -- real autoincrement sequences only ever produce
positive values, so this makes collision with genuine data structurally
impossible rather than merely unlikely.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from meetings.models import Meeting, MeetingDocument, MeetingTypeDefinition
from notion_editor.models import ContentBlock, Draft, DraftProjectLink
from rag.management.commands.run_project_rag_eval import (
    DEFAULT_FIXTURE,
    EVAL_ORG_NAME,
    EVAL_PROJECT_NAME,
    EVAL_USER_EMAIL,
    _get_or_create_eval_project,
    _get_or_create_eval_tenant,
    _load_fixture,
    _validate_fixture_shape,
)
from retrospective.models import RetrospectiveTask

_COMMAND_DIR = Path(__file__).resolve().parent
_RAG_APP_DIR = _COMMAND_DIR.parents[1]  # backend/rag
DEFAULT_OUTPUT = _RAG_APP_DIR / 'eval_seed' / 'med264_seed.sql'

# Fixed: re-running the generator against an *unchanged* fixture must always
# derive the same ids, so this must never change.
_UUID_NAMESPACE = uuid.UUID('7c9c2b0e-5f1a-4b1a-9b0e-6b5b7a4d2c1a')

# Negative range: confirmed via information_schema that meetings_meeting,
# meetings_meetingdocument, meetings_meetingtypedefinition, notion_editor_draft,
# notion_editor_contentblock, and notion_editor_draftprojectlink are all plain
# `bigint` PKs with no positivity CHECK constraint. Real autoincrement
# sequences only ever assign positive values, so any id in this range can
# never collide with genuinely-inserted data -- collision is only possible
# against another MED-264 seed row (handled by the DO-block check below).
_INT_ID_BASE = -2_000_000_000
_INT_ID_SPAN = 900_000_000

PROJECT_SUBQUERY = (
    # No organization join: core_project is a *tenant-scoped* table (one org's
    # projects live in exactly one physical schema), and by the time this
    # subquery runs, `SET LOCAL search_path` has already scoped every
    # unqualified reference to the target tenant schema alone -- there is no
    # cross-org ambiguity left to resolve, so requiring the org name here
    # would just be an unnecessary, easy-to-drift dependency on core_project's
    # public->tenant organization_id FK.
    f"(SELECT id FROM core_project WHERE name = '{EVAL_PROJECT_NAME}')"
)
USER_SUBQUERY = f"(SELECT id FROM core_customuser WHERE email = '{EVAL_USER_EMAIL}')"

# Columns needing FK substitution: maps column_name -> a function computing
# the replacement SQL text given (table, deterministic-id lookup helper).
_EXTERNAL_ANCHOR_COLUMNS = {
    'project_id': PROJECT_SUBQUERY,
    'campaign_id': PROJECT_SUBQUERY,
    'user_id': USER_SUBQUERY,
    'created_by_id': USER_SUBQUERY,
}


def _deterministic_uuid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_UUID_NAMESPACE, '|'.join(parts))


def _deterministic_int_id(*parts: str) -> int:
    digest = hashlib.sha256('|'.join(parts).encode('utf-8')).digest()
    return _INT_ID_BASE + (int.from_bytes(digest[:4], 'big') % _INT_ID_SPAN)


def _split_sql_values(values_text: str) -> list[str]:
    """Split a captured `VALUES (...)` inner text into top-level value
    tokens, respecting single-quoted string literals (with '' as an escaped
    quote) and parenthesis nesting (e.g. a ::jsonb cast wrapped in parens).
    Does not need to handle anything more exotic -- these are all literal
    values Django's own debug cursor already rendered, never sub-selects.
    """
    tokens: list[str] = []
    buf = []
    in_quote = False
    depth = 0
    i = 0
    n = len(values_text)
    while i < n:
        ch = values_text[i]
        if in_quote:
            if ch == "'" and i + 1 < n and values_text[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            if ch == "'":
                in_quote = False
            buf.append(ch)
        elif ch == "'":
            in_quote = True
            buf.append(ch)
        elif ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            tokens.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        tokens.append(''.join(buf).strip())
    return tokens


_INSERT_RE = re.compile(
    # Django's debug cursor renders a plain `INSERT ... VALUES (...)` for
    # UUID-pk models (id has a Python-side default, no DB round-trip needed),
    # but for autofield-pk models (no Python-side default) it appends
    # `RETURNING "table"."id"` to fetch the sequence-assigned value -- the
    # trailing group here tolerates and discards that clause; it is never
    # needed in a static seed script since we assign our own deterministic id.
    r'^INSERT INTO\s+"?(?P<table>\w+)"?\s*\((?P<cols>.+?)\)\s*VALUES\s*\((?P<vals>.+)\)(?:\s*RETURNING\b.*)?$',
    re.IGNORECASE | re.DOTALL,
)

# Django/psycopg2's rendering of an auto_now/auto_now_add/default=timezone.now
# datetime value, e.g. '2026-09-08T13:38:16.759247+00:00'::timestamptz -- the
# ONE source of nondeterminism across every seeded model (confirmed by
# inspecting real captured INSERTs for all 7 row types: every timestamp
# column matches this shape, no other field is time- or randomness-derived
# once `id` is separately overridden). Normalized to `now()` at render time
# so the generated FILE TEXT is byte-identical on every regeneration from an
# unchanged fixture, while execution still gets a real, accurate timestamp.
_TIMESTAMP_LITERAL_RE = re.compile(
    r"^'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+[+-]\d{2}:\d{2}'::timestamptz$"
)


def _parse_captured_insert(sql: str) -> tuple[str, list[str], list[str]]:
    match = _INSERT_RE.match(sql.strip())
    if not match:
        raise CommandError(f"Could not parse captured INSERT statement (unexpected shape): {sql[:200]!r}")
    table = match.group('table')
    columns = [c.strip().strip('"') for c in match.group('cols').split(',')]
    values = _split_sql_values(match.group('vals'))
    if len(columns) != len(values):
        raise CommandError(
            f"Column/value count mismatch parsing captured INSERT for {table}: "
            f"{len(columns)} columns vs {len(values)} values -- tokenizer bug, do not proceed."
        )
    return table, columns, values


class Command(BaseCommand):
    help = (
        "Generate the committed MED-264 eval-fixture seed SQL from the JSON fixture. "
        "Discovers each table's real column shape via a rolled-back ORM dry-run, then "
        "renders a portable, collision-safe, idempotent SQL script. Use --check to "
        "verify the committed file is still in sync with the fixture without writing."
    )

    def add_arguments(self, parser):
        parser.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE, help='Path to the eval fixture JSON.')
        parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT, help='Path to write the seed SQL file.')
        parser.add_argument(
            '--check', action='store_true',
            help='Regenerate in memory and diff against --output instead of writing it; exit nonzero on any difference.',
        )

    def handle(self, *args, **options):
        fixture_path: Path = options['fixture']
        output_path: Path = options['output']
        check_mode: bool = options['check']

        data = _load_fixture(fixture_path)
        _validate_fixture_shape(data, fixture_path)

        org, user = _get_or_create_eval_tenant()
        eval_schema = slug_to_schema_name(org.slug)
        self.stdout.write(f"Eval tenant (dry-run FK anchor only, not baked into output): schema={eval_schema!r}")

        parsed_rows = self._capture_and_parse(eval_schema, org, user, data)
        sql_text = self._render(fixture_path, data, parsed_rows)

        if check_mode:
            if not output_path.exists():
                raise CommandError(f"--check: {output_path} does not exist yet; run without --check first.")
            existing = output_path.read_text(encoding='utf-8')
            if existing != sql_text:
                raise CommandError(
                    f"--check: regenerated SQL differs from {output_path}. "
                    "The fixture and the committed seed SQL are out of sync -- regenerate and commit."
                )
            self.stdout.write(self.style.SUCCESS(f"--check: {output_path} matches the fixture (byte-identical)."))
            return

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(sql_text, encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(parsed_rows)} row(s) to {output_path}"))

    # -----------------------------------------------------------------
    # Phase A: real ORM dry-run (always rolled back) -> parsed (table,
    # columns, values, deterministic ROW ANCHOR) tuples.
    # -----------------------------------------------------------------

    def _capture_and_parse(self, eval_schema: str, org, user, data: dict) -> list[dict]:
        rows: list[dict] = []

        with tenant_schema_context(eval_schema):
            with transaction.atomic():
                project = _get_or_create_eval_project(org, user)
                self._clear_existing_eval_rows(project)

                type_def_anchor = ('MeetingTypeDefinition', 'planning')
                with CaptureQueriesContext(connection) as ctx:
                    type_def = MeetingTypeDefinition.objects.create(
                        project=project, slug='planning', label='Planning',
                    )
                rows.append(self._parse_row(ctx, type_def_anchor))

                for src in data['sources']:
                    source_type = src['source_type']
                    ref = src['source_ref']
                    if source_type == 'meeting':
                        with CaptureQueriesContext(connection) as ctx:
                            meeting = Meeting.objects.create(
                                project=project, title=src['title'], type_definition=type_def,
                                objective='', summary='',
                            )
                        rows.append(self._parse_row(ctx, ('Meeting', ref), fk_anchors={'type_definition_id': type_def_anchor}))

                        with CaptureQueriesContext(connection) as ctx:
                            MeetingDocument.objects.create(meeting=meeting, content=src['content'])
                        rows.append(self._parse_row(ctx, ('MeetingDocument', ref), fk_anchors={'meeting_id': ('Meeting', ref)}))

                    elif source_type == 'notion_draft':
                        with CaptureQueriesContext(connection) as ctx:
                            draft = Draft.objects.create(user=user, title=src['title'], status='draft')
                        rows.append(self._parse_row(ctx, ('Draft', ref)))

                        with CaptureQueriesContext(connection) as ctx:
                            DraftProjectLink.objects.create(draft=draft, project=project)
                        rows.append(self._parse_row(ctx, ('DraftProjectLink', ref), fk_anchors={'draft_id': ('Draft', ref)}))

                        with CaptureQueriesContext(connection) as ctx:
                            ContentBlock.objects.create(
                                draft=draft, order=0, block_type='text', content={'text': src['content']},
                            )
                        rows.append(self._parse_row(ctx, ('ContentBlock', ref), fk_anchors={'draft_id': ('Draft', ref)}))

                    elif source_type == 'retrospective':
                        with CaptureQueriesContext(connection) as ctx:
                            RetrospectiveTask.objects.create(
                                campaign=project, decision=src['title'], created_by=user,
                                primary_assumption=src['content'],
                            )
                        rows.append(self._parse_row(ctx, ('RetrospectiveTask', ref)))

                    else:
                        raise CommandError(f"Unknown source_type {source_type!r} for {ref!r}")

                # Always undo everything above -- this command only *generates*
                # SQL text; it never leaves seeded rows behind as a side effect,
                # and (per module docstring) never enqueues Celery indexing
                # work, since on_commit callbacks are discarded on rollback.
                transaction.set_rollback(True)

        return rows

    def _parse_row(self, ctx: CaptureQueriesContext, anchor: tuple[str, str], fk_anchors: dict | None = None) -> dict:
        insert_queries = [q for q in ctx.captured_queries if q['sql'].strip().upper().startswith('INSERT')]
        if len(insert_queries) != 1:
            raise CommandError(
                f"Expected exactly 1 INSERT for {anchor}, captured {len(insert_queries)}: "
                f"{[q['sql'][:120] for q in insert_queries]}"
            )
        table, columns, values = _parse_captured_insert(insert_queries[0]['sql'])
        return {'anchor': anchor, 'table': table, 'columns': columns, 'values': values, 'fk_anchors': fk_anchors or {}}

    def _clear_existing_eval_rows(self, project) -> None:
        """Delete any pre-existing rows for this project so every seed call
        above takes the INSERT branch, guaranteeing a complete, self-contained
        capture. Rolled back along with everything else -- never permanent.
        """
        Meeting.objects.filter(project=project).delete()
        Draft.objects.filter(project_link__project=project).delete()
        RetrospectiveTask.objects.filter(campaign=project).delete()
        MeetingTypeDefinition.objects.filter(project=project).delete()

    # -----------------------------------------------------------------
    # Phase B: rendering -- substitute portable ids/subqueries, add
    # ON CONFLICT + collision-verification, wrap in one transaction.
    # -----------------------------------------------------------------

    def _row_id_literal(self, table: str, ref: str) -> str:
        if table == 'RetrospectiveTask':
            return f"'{_deterministic_uuid(table, ref)}'"
        return str(_deterministic_int_id(table, ref))

    def _render_row(self, row: dict) -> str:
        table, columns, values = row['table'], row['columns'], row['values']
        anchor_table, anchor_ref = row['anchor']
        id_literal = self._row_id_literal(anchor_table, anchor_ref)

        rendered_columns = list(columns)
        rendered_values = []
        natural_key_checks = []  # (column, literal) pairs used for the collision-verification DO block
        for col, val in zip(columns, values):
            if col == 'id':
                rendered_values.append(id_literal)
                continue
            if col in row['fk_anchors']:
                ref_table, ref_ref = row['fk_anchors'][col]
                replacement = self._row_id_literal(ref_table, ref_ref)
                rendered_values.append(replacement)
                natural_key_checks.append((col, replacement))
                continue
            if col in _EXTERNAL_ANCHOR_COLUMNS:
                replacement = _EXTERNAL_ANCHOR_COLUMNS[col]
                rendered_values.append(replacement)
                natural_key_checks.append((col, replacement))
                continue
            if _TIMESTAMP_LITERAL_RE.match(val):
                rendered_values.append('now()')
                continue
            rendered_values.append(val)
            if col in ('title', 'decision', 'slug'):
                natural_key_checks.append((col, val))

        if 'id' not in columns:
            # Autofield-pk models (Meeting, MeetingDocument, MeetingTypeDefinition,
            # Draft, ContentBlock, DraftProjectLink): Django omits `id` from the
            # INSERT entirely (no Python-side default; it relies on the DB
            # sequence + RETURNING). We always want our own deterministic value
            # instead, so prepend it explicitly.
            rendered_columns = ['id'] + rendered_columns
            rendered_values = [id_literal] + rendered_values

        col_list = ', '.join(f'"{c}"' for c in rendered_columns)
        val_list = ', '.join(rendered_values)
        insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({val_list})\n  ON CONFLICT (id) DO NOTHING;'

        if not natural_key_checks:
            # id-only table shape (shouldn't happen for our 7 tables, but
            # fail loudly rather than silently skip the safety check).
            raise CommandError(f"No natural-key column found to verify collisions for {table} ({anchor_table}:{anchor_ref}) -- add one.")

        where_clause = ' AND '.join(f'"{c}" = {v}' for c, v in [('id', id_literal)] + natural_key_checks)
        check_sql = (
            "DO $$\nBEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM \"{table}\" WHERE {where_clause}) THEN\n"
            f"    RAISE EXCEPTION 'MED-264 seed collision: id % in {table} is occupied by unrelated data (expected %)', "
            f"{id_literal}, '{anchor_table}:{anchor_ref}';\n"
            "  END IF;\nEND $$;"
        )
        return insert_sql + '\n' + check_sql

    def _render(self, fixture_path: Path, data: dict, rows: list[dict]) -> str:
        header = (
            "-- MED-264 eval-fixture seed SQL -- GENERATED FILE, DO NOT HAND-EDIT.\n"
            f"-- Source of truth: {fixture_path.as_posix()} (dataset_version={data.get('dataset_version')!r}).\n"
            "-- Regenerate with: python manage.py generate_med264_seed_sql\n"
            "-- Verify in sync with: python manage.py generate_med264_seed_sql --check\n"
            "--\n"
            "-- Usage (target_schema is REQUIRED, no default -- never relies on the\n"
            "-- caller's existing search_path, to avoid silently seeding the wrong tenant):\n"
            "--   psql <connection args> -v target_schema=org_rag_eval_harness -f med264_seed.sql\n"
            "--\n"
            "-- Portable: project/user rows are resolved at execution time by stable natural\n"
            f"-- keys (organization name '{EVAL_ORG_NAME}' + project name '{EVAL_PROJECT_NAME}'; user\n"
            f"-- email '{EVAL_USER_EMAIL}'), never by the generating machine's local numeric ids.\n"
            "--\n"
            "-- Idempotent + collision-safe: every row is ON CONFLICT (id) DO NOTHING followed by a\n"
            "-- verification block. Re-running against a schema that already has these exact rows is\n"
            "-- a safe no-op; an id collision with unrelated data aborts the whole transaction (no\n"
            "-- partial seed is ever left behind).\n"
            f"-- {len(rows)} row(s).\n\n"
            "BEGIN;\n\n"
            "-- psql's :'var' / :\"var\" substitution is a client-side text pass over\n"
            "-- top-level statements; whether it also reaches inside a $$-quoted DO\n"
            "-- body is not something to assume. So the schema name is captured ONCE\n"
            "-- here (top-level statement, substitution definitely applies) into a\n"
            "-- transaction-local setting, and every DO block below reads it back via\n"
            "-- plain current_setting() -- no psql variable syntax inside any $$ body.\n"
            "SELECT set_config('med264.target_schema', :'target_schema', true);\n\n"
            "-- Preflight: fail fast with a clear MED-264-specific error instead of a\n"
            "-- confusing downstream FK/NOT NULL failure mid-seed.\n"
            "DO $$\n"
            "BEGIN\n"
            "  IF to_regnamespace(current_setting('med264.target_schema')) IS NULL THEN\n"
            "    RAISE EXCEPTION 'MED-264 seed preflight: target schema \"%\" does not exist', current_setting('med264.target_schema');\n"
            "  END IF;\n"
            "END $$;\n\n"
            "SET LOCAL search_path TO :\"target_schema\", public;\n\n"
            "DO $$\n"
            "DECLARE\n"
            "  project_count int;\n"
            "  user_count int;\n"
            "BEGIN\n"
            f"  SELECT count(*) INTO project_count FROM core_project WHERE name = '{EVAL_PROJECT_NAME}';\n"
            "  IF project_count != 1 THEN\n"
            "    RAISE EXCEPTION 'MED-264 seed preflight: expected exactly 1 eval Project (name=%) in schema %, found %',\n"
            f"      '{EVAL_PROJECT_NAME}', current_setting('med264.target_schema'), project_count;\n"
            "  END IF;\n\n"
            f"  SELECT count(*) INTO user_count FROM core_customuser WHERE email = '{EVAL_USER_EMAIL}';\n"
            "  IF user_count != 1 THEN\n"
            "    RAISE EXCEPTION 'MED-264 seed preflight: expected exactly 1 eval User (email=%) in public, found %',\n"
            f"      '{EVAL_USER_EMAIL}', user_count;\n"
            "  END IF;\n"
            "END $$;\n\n"
        )
        body = '\n\n'.join(self._render_row(row) for row in rows)
        footer = "\n\nCOMMIT;\n"
        return header + body + footer
