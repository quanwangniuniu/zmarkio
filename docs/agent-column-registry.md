# Agent column registry guidance

Column definitions describe spreadsheet headers; they do not create database
columns. A duplicate can silently change which canonical name or category a
header resolves to.

Canonical names and aliases must be unambiguous **within one schema/template**.
Different export formats may share names such as `impressions`. Schema keys,
however, must be unique within the process registry. Matching ignores case,
leading/trailing spaces, and differences between spaces and underscores.

Plugin startup code must register schemas and columns through the guarded API:

```python
from agent.column_registry import register_schema, register_column

register_schema(
    "my_plugin",
    {
        "name": "My Plugin Export",
        "source_hint": "My Plugin",
        "columns": [],
    },
)
register_column(
    "my_plugin",
    "my_metric",
    {
        "aliases": ["My metric", "my_metric"],
        "category": "performance_ratio",
        "description": "A metric exported by My Plugin",
    },
)
```

For bulk registration, supply `columns` as a list of `(canonical_name, spec)`
pairs. This preserves duplicate declarations until validation; Python dict
literals discard exact duplicate keys before any validator can inspect them.
Mappings are accepted for compatibility, but incremental registration or pairs
provide stronger duplicate detection. Built-in declarations use pairs.

`SCHEMA_REGISTRY` and its nested definitions are read-only. Registration builds
and validates both definitions and indexes before publishing a single snapshot.
A malformed candidate cannot partially update live state. A duplicate schema
key, canonical name, or ambiguous alias raises `ColumnRegistryCollisionError`.

Rejected collisions are retained as a process-level startup failure: detection
stops even though the last valid definitions remain intact. `manage.py check`
reports `agent.E001`. Built-in validation does not raise out of Django's
`ready()` hook, allowing diagnostics to run. Django system checks execute after
all app `ready()` hooks; the agent hook itself is not a post-plugin lifecycle hook.

There is no plugin loader in this repository. Integrations must call these APIs
from their registration code. If that code lets an exception escape during
startup, Django cannot serve diagnostics; the frontend then reports that the
backend is unreachable rather than falsely claiming a confirmed collision.

Registry state survives `importlib.reload(agent.column_registry)`. Re-running a
plugin's registration for an existing schema/column raises; it is not an implicit
replace operation. After fixing plugin definitions, restart the backend process
to clear the retained failure and register the corrected definitions. Django's
normal process-restarting development reloader also creates fresh state.

## Database templates and admin diagnostics

`DataSchemaTemplate.clean()` and `save()` validate canonical names and aliases;
Django admin shows validation errors on the `column_definitions` field. Bulk ORM
updates/raw SQL bypass model hooks, so templates are also validated before
matching and when the diagnostics endpoint reads them. Existing corrupt rows
are rejected rather than silently used. No migration is required.

The agent panel shows diagnostics to staff and organisation admins. While open,
it checks on mount, every 30 seconds, and on window focus. It distinguishes a
confirmed collision from an unreachable backend. Repairing a database template
clears its diagnostic error on the next check; plugin registration failures
require the process restart described above.

## Testing and QA

Tests that intentionally build overlapping fixtures may set
`AGENT_COLUMN_REGISTRY_TEST_MODE=1`. This flag is only an escape hatch for
tests; it must not be enabled in development, staging, or production.
The flag deliberately permits last-registration-wins behavior and is not
automatically enabled just because pytest is running.

Run the existing backend pytest suite with the project's configured test
database, including `agent/tests_column_registry.py` and `agent/test_executors.py`.
Frontend coverage is in
`src/__tests__/components/AgentRegistryStatusBanner.test.tsx` (Jest/Testing Library).

For a live demo in a disposable environment:

1. Open the agent panel as an admin and show the healthy state.
2. In Django admin, edit a template so two different canonical names share an
   alias; saving must show a field error rather than persist the collision.
3. To exercise legacy-data detection, use a disposable template and a deliberately
   bypassed model hook (bulk update) to introduce that same conflict. Reopen/focus
   the panel to show the diagnostic; detection must reject the template too.
4. Repair/delete the disposable template and focus the panel to show recovery.
5. Demonstrate plugin duplicate registration, rollback, reload persistence, and
   test-mode opt-out with the regression tests. A Django shell is a separate
   process and cannot change the running web worker's in-memory registry.
