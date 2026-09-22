---
paths:
  - "backend/**/*.py"
---

# Migrations & gotchas

Migration hygiene and the backend traps that CI (or a silent regression) will punish.

## Migrations

- Run `makemigrations <app>` and **commit** the numbered migration file. Production does **not**
  auto-migrate — an uncommitted migration means a broken deploy.
- **Never edit, delete, or rename an existing `backend/**/migrations/*.py`**, and never re-add
  operations to a no-op migration. CI's `migration_guard` blocks all of these.
- New org-scoped models also need registration in `core/tenant_config.py` and
  `python manage.py migrate_all_tenants` — see `.claude/rules/multi-tenancy.md`.

## Gotchas

- **Never edit any `docker-compose*.yml` file** — run the command yourself or tell the user what to
  run.
- **Do not add `testpaths` to `backend/pytest.ini`.** A past regression silently cut CI from ~4,500
  to ~1,100 tests; there is a warning comment in the file.
- **No backend linter or formatter is configured** — match the style of surrounding code
  (`.claude/rules/code-style.md`). Use `logger = logging.getLogger(__name__)`, **never `print()`**.
- Do not use a bare `except Exception` that returns a 500 with `str(e)` — it leaks internals and
  swallows real errors.
- DRF's global default permission is `AllowAny`; **every view must set `permission_classes`**
  (`.claude/rules/permissions-security.md`).
- JWT `APIView`s need `@method_decorator(csrf_exempt, name='dispatch')`
  (`.claude/rules/permissions-security.md`).
- **Update the hand-maintained specs in `openapi/openapi_spec/`** whenever you change an API's
  contract — there is no runtime schema generation.

## Observability & audit

- Instrument with a per-app `metrics.py` built on `prometheus_client` primitives; keep label
  cardinality bounded.
- Emit audit events via `core.services.audit_events.safe_emit_audit_event(...)`.
