---
paths:
  - "variations-studio-api/**/*.ts"
---

# variations-studio-api

A secondary Next.js (App Router) service that generates ad-copy variations. It has **no React
UI of its own** — it is an API service. Prisma + PostgreSQL, TypeScript `strict`. Deps are
deliberately minimal (`next`, `react`, `@prisma/client`, `jose`). It shares the backend's
Postgres and validates **Django-minted JWTs** — it does not mint its own.

Principle here too: **thin edges, fat core.** Route handlers parse/authorize and delegate;
logic lives in `lib/` and `src/`.

## Layering

- **`app/api/**/route.ts`** — thin HTTP edge: authorize → parse body/params → call a `lib/` or
  `src/domains` function → `NextResponse.json(...)`. Reference: `app/api/ad_copy_variation/
  variations/route.ts`, `app/api/me/route.ts`.
- **`lib/`** — request-scoped services (`auth.ts`, `tenant.ts`, `projects.ts`,
  `variationCreate.ts`, `variationStore.ts`, `prisma.ts`, …).
- **`src/`** — deeper core: `src/domains/*` (orchestration), `src/repo/*` (Prisma data access),
  `src/platform/http/*` (shared `ApiError`, `readJsonBody`, param parsing), `src/ai/*`
  (providers/prompts). Import via the `@/*` alias, which maps to the package root (not `src/` —
  so `@/lib/...` and `@/src/...`).

## Auth & tenancy (do not weaken)

- Every protected route starts with `requireAccessUser(request)` or `requireStudioContext(request)`
  from `@/lib/auth` / `@/lib/tenant`, then `if (isAuthFailure(x)) return NextResponse.json({
  detail: x.error }, { status: x.status })`.
- JWT rules must stay in **parity with Django** (`backend/backend/settings.py` SIMPLE_JWT and
  `core/authentication.py`): HS256 only, key from `process.env.SECRET_KEY`, require
  `token_type === 'access'`, resolve an active user, and enforce `auth_token_version` for
  revocation. DON'T add leeway, accept refresh tokens, or invent claims.
- Prisma bakes the schema into SQL and ignores `search_path`. Tenant-scoped tables
  (`AdCopyVariation`, `core_project`, `core_projectmember`) MUST be reached with raw SQL
  qualified via `tenantTable(schema, table)` from `@/lib/tenant`; everything in `schema.prisma`
  lives in `public`. Never interpolate an unvalidated schema/table name into raw SQL.

## Errors & responses

- Throw `ApiError(status, message, field)` from `@/src/platform/http` and convert at the edge
  with `responseFromUnknown(err)`. Response bodies mirror DRF: `{ detail }` for auth failures,
  field-keyed objects for validation, `{ results, count, page, page_size }` for lists.

## Testing

Jest, preceded by `npx prisma generate && npx tsc --noEmit` (matches CI). JWT parity is proven
against Django-minted fixtures (`issue_studio_jwt_fixtures` → `DJANGO_JWT_FIXTURES_PATH`). See
`.claude/rules/testing.md`.

## Related rules

- `.claude/rules/code-style.md` (TS style), `.claude/rules/testing.md`,
  `.claude/rules/multi-tenancy.md` (the Django side of schema-per-org).
