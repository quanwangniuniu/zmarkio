# Agent column registry guidance

Agent column schemas are shared by every spreadsheet plugin. A canonical name
or alias must identify one column only across the whole registry; otherwise a
lookup can depend on plugin load order.

Plugin startup code must register schemas and columns through the guarded API:

```python
from agent.column_registry import register_schema, register_column

register_schema(
    "my_plugin",
    {
        "name": "My Plugin Export",
        "source_hint": "My Plugin",
        "columns": {},
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

Registration validates the candidate registry before changing the live lookup
indexes. Duplicate canonical names and aliases raise
`ColumnRegistryCollisionError` in normal application mode. Django also runs
the same validation during agent startup and through `manage.py check`, so a
hot-reloaded plugin cannot silently replace an existing column.

Tests that intentionally build overlapping fixtures may set
`AGENT_COLUMN_REGISTRY_TEST_MODE=1`. This flag is only an escape hatch for
tests; it must not be enabled in development, staging, or production.
