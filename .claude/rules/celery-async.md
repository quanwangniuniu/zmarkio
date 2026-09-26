---
paths:
  - "backend/**/*.py"
---

# Celery & async

Covers Celery tasks and Django Channels (WebSocket) consumers. Both run **outside**
`TenantSchemaMiddleware`, so both must set tenant context explicitly.

## Celery tasks

- Define tasks with `@shared_task` — **never `@app.task`**.
- Retries: `bind=True` + `max_retries` / `default_retry_delay`, or `autoretry_for`. Use
  `max_retries=0` for jobs that must not silently retry.
- Tasks are **thin wrappers**: resolve arguments, call a service function in `services.py`, done. No
  business logic, complex branching, or multi-step domain workflows in the task body.
- The Beat schedule is **static** in `settings.CELERY_BEAT_SCHEDULE` — not in `celery.py`. Chat tasks
  route to dedicated queues via `CELERY_TASK_ROUTES`.
- **In tests Celery is not eager.** Patch the task at its call site (e.g.
  `mocker.patch('myapp.services.process_thing.delay')`) rather than expecting it to run inline.

## Tenant context in tasks

Any task touching tenant-scoped models MUST accept a `tenant_schema` kwarg and wrap ORM work in
`core.tenant_context.tenant_schema_context`:

```python
from celery import shared_task
from core.tenant_context import tenant_schema_context

@shared_task
def process_thing(thing_id, tenant_schema='public'):
    with tenant_schema_context(tenant_schema):
        ...  # ORM calls here
```

Full rules (registration, raw SQL, cross-schema FKs): `.claude/rules/multi-tenancy.md`.

## WebSocket consumers (Channels)

- Subclass `core.consumers.InstrumentedAsyncWebsocketConsumer` and declare a class-level
  `ws_channel` label — the label set is bounded and a test enforces it.
- Auth goes through the shared `asset.middleware.JWTAuthMiddleware` (token from the `Authorization`
  header or `?token=`). Spreadsheet rooms use one-time tickets instead.
- Run every synchronous ORM call inside `core.tenant_context.tenant_schema_context` — the middleware
  is not active in the ASGI consumer lifecycle.
- Register the app's `routing.py` in `backend/backend/asgi.py`.
