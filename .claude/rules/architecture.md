---
paths:
  - "backend/**/*.py"
---

# Architecture & layering

Principle: **thin edges, fat core.** Views and pages stay thin; business logic lives in
`services.py`.

## Layer responsibilities

- **Views**: parse input → check permissions → call a service function → return `Response`.
  - DON'T: put business logic, ORM queries beyond `get_queryset`, or branching domain rules
    in views.
- **Services** (`services.py`): all business logic. Return plain data (dicts, model instances,
  querysets). Raise exceptions on failure.
  - DON'T: import `Response`, `status`, or any DRF HTTP object inside a service module.
- **Serializers**: validate shape and coerce types only — pure data transformers.
  - DON'T: call service functions, trigger signals, or perform side effects inside
    `.save()` / `.create()` / `.update()`.

## Service function conventions

### Modern style (all new code)

- Module-level functions, verb-first name: `create_support_project`, `reorder_work_types`.
- Keyword-only args after the first positional:
  `def f(project_id, *, name, sort_order=None):`.
- `@transaction.atomic` on any function that does multiple writes.
- `_`-prefixed private helpers (`_assert_unique_name`, `_next_sort_order`).
- Function-local imports to break circular dependencies — keep this pattern where you see it.

### Legacy style (do NOT create new ones)

- `class XService` with `@staticmethod`s taking a `data` dict.
- When editing legacy code, match the file's existing style — do not mix modern functions into a
  legacy class within the same module.

## Error flow

- Services raise `django.core.exceptions.ValidationError({'field': 'message'})`.
- Views catch and re-raise as `rest_framework.exceptions.ValidationError` (see
  `_raise_drf_validation` in `csm/views.py`).
- DON'T: raise DRF `ValidationError` from inside a service — it couples the service to HTTP.
- DON'T: return `{'error': str(e)}` dicts from services.

## Celery tasks

- Tasks are thin wrappers: resolve arguments, call a service function, done.
- DON'T: put business logic, complex branching, or multi-step domain workflows in a task body.
- When the task needs tenant context, see `.claude/rules/multi-tenancy.md`.

## Cross-app dependencies

- DO: import another app's **service functions**
  (`from csm.services.support_channels import build_channel_runtime_config`).
- DON'T: reach into another app's models and run custom querysets — that couples apps at the
  data layer. The owning app should expose the data through a service function.

## Splitting services.py

- When `services.py` exceeds ~400 lines or covers multiple distinct sub-domains, convert to a
  `services/` package with one submodule per domain slice.
- Re-export the public API from `services/__init__.py` so existing imports stay valid.
- Reference: `csm/services/` is the canonical example.
