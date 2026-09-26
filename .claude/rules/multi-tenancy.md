---
paths:
  - "backend/**/*.py"
---

# Multi-tenancy (schema-per-org)

Custom implementation — no `django-tenants`. Each organization gets a Postgres schema
(`org_{slug}`). `core.middleware.tenant_schema.TenantSchemaMiddleware` sets `search_path` per
HTTP request and resets to `public` afterward. **Background workers and WebSocket consumers do
NOT pass through that middleware** — they must set tenant context explicitly.

## Tenant model registration

- MUST: when adding an org-scoped model, import it into `core/tenant_config.py` and add it to
  `get_tenant_models()` **after all its FK dependencies** (the list is in topological order).
- MUST: after adding, run `python manage.py migrate_all_tenants` for existing orgs. New org
  provisioning picks it up automatically.
- DON'T: add shared/global models to that list. Models that stay in `public`: Organization,
  CustomUser, OrganizationMembership, auth/audit tables, integrations, billing.

## Schema context outside HTTP requests

### Celery tasks

Tasks run outside `TenantSchemaMiddleware`. Any task that touches tenant-scoped models MUST:

1. Accept `tenant_schema` as a keyword argument.
2. Wrap ORM work in `core.tenant_context.tenant_schema_context(tenant_schema)`.

```python
from core.tenant_context import tenant_schema_context

@shared_task
def process_thing(thing_id, tenant_schema='public'):
    with tenant_schema_context(tenant_schema):
        ...  # ORM calls here
```

### WebSocket consumers

Consumers MUST use `tenant_schema_context` for every synchronous ORM call (see
`core.tenant_context`). The middleware is not active in the ASGI consumer lifecycle.

### Management commands

Commands that iterate tenants: query `Organization.objects.filter(is_active=True)` from
`public`, then use `tenant_schema_context` per org. DON'T assume the command's default
`search_path` includes tenant tables.

## User lookups

- `CustomUser` lives in the `public` schema — always.
- `tenant_schema_context` sets `search_path TO <schema>, public`, so user lookups work within
  that context. But in raw SQL, qualify the table: `public.core_customuser`.

## Raw SQL

- DON'T: write raw `SET search_path` in application code. Use `tenant_schema_context`.
- When writing raw SQL that references public-schema tables alongside tenant tables, qualify
  the public table explicitly (`public.core_customuser`, `public.core_organization`).

## Cross-schema foreign keys

- When a tenant model FKs to a public model (e.g. `Project.active_project_user → CustomUser`),
  set `db_constraint=False`. The FK is logical, not enforced at the DB level, because the tables
  live in different schemas (see `core/models.py:281`).
- DON'T: forget `db_constraint=False` — Django's migration will generate an `ALTER TABLE ...
  ADD CONSTRAINT` that fails at runtime.
