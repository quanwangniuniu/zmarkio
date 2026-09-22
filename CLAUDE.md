# CLAUDE.md — MediaJira

Project context for Claude Code. Loaded automatically at the start of every session.
Human setup lives in [README.md](README.md); Docker detail in [DOCKER_README.md](DOCKER_README.md);
CI/CD in [CICD_README.md](CICD_README.md).

## Project overview

MediaJira ("Marketing Simplified", zmarkio.com) is a campaign-management SaaS for media-buying
teams: campaigns, tasks, decisions, real-time chat, calendars, workflow automation, budget
approval, asset management, a spreadsheet engine, AI-agent workflows, and many ad-platform
integrations (Meta, Google Ads, TikTok, Klaviyo, Mailchimp, Slack, Notion, Linear, …).
Multi-tenant: one Postgres schema per organization.

Monorepo with three deployable apps:

- `backend/` — Django project (one app per domain under `backend/<app>/`).
- `frontend/` — Next.js 14 App Router web client.
- `variations-studio-api/` — secondary Next.js service (Prisma, validates Django-minted JWTs).

## Tech stack

- **Backend**: Python 3.10, Django 4.2, Django REST Framework, Django Channels (ASGI/Daphne),
  Celery + Celery Beat, PostgreSQL, Redis, Kafka, Prometheus / OpenTelemetry.
- **Frontend**: Node ≥20, npm, Next.js 14, React 18, TypeScript (`strict`), Tailwind CSS,
  Radix UI, Zustand, Axios, TanStack Query.
- Everything runs in Docker Compose for local dev.

## Repo layout

```
backend/
  <app>/                  one Django app per domain (task/, decision/, campaign/, csm/, ...)
    views.py serializers.py services.py urls.py models.py tests/
  backend/                Django project package (settings.py, urls.py, asgi.py, celery.py)
frontend/
  src/app/                App Router routes/layouts — compose only, no feature logic
  src/components/         React components; common/ ui/ layout/ + feature folders
  src/lib/api/            ALL HTTP client code — one <domain>Api.ts per domain
  src/lib/*Store.ts       Zustand stores (domain-scoped)
  src/types/              shared TypeScript types
  src/hooks/              shared hooks
openapi/openapi_spec/     hand-maintained OpenAPI specs (update alongside API changes)
devops/  k6/  ops/        infra, load tests, deploy scripts
```

## Common commands

All via Docker Compose. Adding `-p mediajira-v2` to every command avoids container-name churn
on rebuilds, but is optional.

```bash
# start / stop / status
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml up -d --build        # after requirements.txt / package.json changes
docker compose -f docker-compose.dev.yml down                 # stop, keep data
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml restart backend      # or: frontend

# logs
docker compose -f docker-compose.dev.yml logs -f backend
docker compose -f docker-compose.dev.yml logs backend --tail 50

# database / Django
docker compose -f docker-compose.dev.yml exec backend python manage.py migrate
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations <app>
docker compose -f docker-compose.dev.yml exec backend python manage.py createsuperuser
docker compose -f docker-compose.dev.yml exec backend python manage.py shell

# tests
docker compose -f docker-compose.dev.yml exec backend python manage.py test          # everyday
docker compose -f docker-compose.dev.yml exec backend python manage.py test <app>     # one app
docker compose -f docker-compose.dev.yml exec backend pytest                          # full suite, matches CI
docker compose -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.dev.yml exec frontend npm test
docker compose -f docker-compose.dev.yml exec frontend npm run build

# shell into a container
docker compose -f docker-compose.dev.yml exec backend bash
docker compose -f docker-compose.dev.yml exec frontend sh
```

Requires a host-machine PostgreSQL on `:5432` (dev containers reach it via `pgbouncer`).
Copy `env.example` → `.env` first.

## Conventions

Topic rules live in `.claude/rules/` and load automatically — `code-style.md` every session,
the rest when Claude opens matching files:

- `.claude/rules/code-style.md` — formatting, naming, language-specific rules.
- `.claude/rules/testing.md` — test strategy, what to mock, coverage expectations.
- `.claude/rules/api-conventions.md` — REST conventions, auth, error shapes.
- `.claude/rules/architecture.md` — layering, service conventions, cross-app deps.
- `.claude/rules/multi-tenancy.md` — schema-per-org rules, tenant context, model registration.
- `.claude/rules/permissions-security.md` — view permissions, CSRF on JWT views, secrets handling.
- `.claude/rules/models-conventions.md` — model/serializer conventions, naming table.
- `.claude/rules/celery-async.md` — Celery tasks and Channels consumers, tenant context.
- `.claude/rules/migrations-gotchas.md` — migration hygiene and backend gotchas.
- `.claude/rules/frontend-architecture.md` — where frontend code goes, client vs server, legacy.
- `.claude/rules/frontend-api-conventions.md` — Axios singleton, `<domain>Api.ts` modules, errors.
- `.claude/rules/frontend-state.md` — Zustand stores, hooks, data-fetching (TanStack is reserved).
- `.claude/rules/frontend-components.md` — shadcn/CVA/Radix, Tailwind tokens, TS types.
- `.claude/rules/variations-studio-api.md` — the secondary Next.js/Prisma service and its JWT auth.

The backend rules load on `backend/**/*.py`; the frontend rules on `frontend/**/*.{ts,tsx}`;
`variations-studio-api.md` on `variations-studio-api/**/*.ts`; `code-style.md` / `testing.md`
apply across apps.

Architecture in one line: **thin edges, fat core** — views and pages stay thin; business logic
lives in `backend/<app>/services.py` and `frontend/src/lib/`.

## Backend structure (Django)

The backend has **two generations** of patterns. New apps and new files use the *modern* style
below. When editing an existing file, match that file so one function doesn't look alien — but
**never newly introduce** any of: header-driven RBAC (`x-user-role` / `x-team-id`), `print()`
debugging, a bare `except Exception` that returns a 500 with `str(e)`, or a serializer
`fields = '__all__'`.

**App layout.** One app per domain under `backend/<app>/`. Standard modules: `models.py`,
`serializers.py`, `views.py`, `urls.py`, `services.py`, `tests/`, `admin.py`. Add `tasks.py`
(Celery), `permissions.py`, `signals.py`, `consumers.py` + `routing.py` (websockets) only when
needed; wire signals in `apps.py` `ready()`. Split `services.py` into a `services/` package once
it grows large (see `csm/services/`). There is no `filters.py` / `selectors.py` convention —
filter inline in `get_queryset`.

**Layering** (detail in `.claude/rules/architecture.md`): thin views → `services.py` →
`serializers.py` (shape/validate only, no side effects). See the architecture rules for service
conventions, error flow, and the modern-vs-legacy distinction.

The rest of the backend conventions live in the path-gated rule files (they load when you open a
`backend/**/*.py` file):

- **Models & serializers** — `TimeStampedModel`, slug mixins, `BigAutoField` PKs, `is_active` over
  soft-delete, `TextChoices`, explicit serializer `fields`, naming: `.claude/rules/models-conventions.md`.
- **Permissions & security** — `AllowAny` default, modern permission stack, CSRF on JWT views,
  secrets/encryption: `.claude/rules/permissions-security.md`.
- **Celery & async** — `@shared_task` rules, Beat schedule, Channels consumers, tenant context:
  `.claude/rules/celery-async.md`.
- **Migrations & gotchas** — migration-guard rules plus the backend traps, observability/audit, and
  style pointers: `.claude/rules/migrations-gotchas.md`.
- **Multi-tenancy** (schema-per-org, custom — no `django-tenants`): `.claude/rules/multi-tenancy.md`.

## Git / PR

- **Claude does not stage, commit, or push.** The `.claude/settings.json` deny-list blocks the
  write/history/working-tree git verbs (`add`, `commit`, `push`, `reset`, `restore`, `checkout`,
  `clean`, `stash`, …); a human runs them. Claude may prepare diffs, commit messages, and PR text.
- Conventional Commits with the Jira key: `feat(csm): add queue routing (MED-412)`.
- Branch flow for a developer: **feature branch → reviewed PR → `prod-preview`**. That is the
  whole job. The `prod-preview` → `main` promotion is the release owner's, not a developer's —
  never open, prepare, or drive that PR. New PRs target `prod-preview`, never `main` (CI blocks
  feature branches from `main` anyway).
- Jira project keys in use: `SMP`, `MED`, `BGF`, `DX`.
- Reusable workflows: `/review` (PR review), `/fix-issue <issue-or-JIRA-key>` (issue → review-ready
  change; Claude stops before staging/committing/pushing). `/deploy` explains the release
  process (informational — see `.claude/skills/deploy/`).

## Gotchas

Always-relevant, cross-cutting traps (full backend list — `testpaths`, `openapi/`, no-linter, CSRF
— in `.claude/rules/migrations-gotchas.md`):

- Never edit any `docker-compose*.yml` file — run commands or tell the user what to run.
- Never delete or rename an existing `backend/**/migrations/*.py` — CI's `migration_guard`
  blocks it. Always commit new migration files.
- DRF's global default permission is `AllowAny`; **every view must set `permission_classes`**.

## Personal overrides

Machine-specific notes (local DB creds, personal tool paths) go in `CLAUDE.local.md`, which is
gitignored. See `CLAUDE.local.md.example` for a starting point.

## MCP servers

`.mcp.json` (committed) declares four project MCP servers. No secrets live in it — the three
hosted servers use OAuth, and the database server reads a connection string from an env var.
Each developer authenticates once:

| Server | Auth — one-time per developer |
| --- | --- |
| `github` | Run `/mcp` → `github` → authorize in the browser (GitHub OAuth). Org blocks OAuth apps? See `.mcp.json.example` for the PAT variant. |
| `linear` | Run `/mcp` → `linear` → authorize (Linear OAuth). |
| `atlassian` | Run `/mcp` → `atlassian` → authorize (Atlassian OAuth). Covers the `SMP` / `MED` / `BGF` / `DX` Jira projects. |
| `postgres` | Read-only (`--access-mode=restricted`) against your **local dev DB** via a throwaway Docker container. Set `MCP_DATABASE_URI` in `.claude/settings.local.json` under an `"env"` key: `postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@host.docker.internal:5432/<POSTGRES_DB>` (values from your `.env`). Needs Docker running. `.mcp.json.example` has a no-Docker (`uvx`) variant. |

Check status anytime with `/mcp`. `.mcp.json.example` documents every alternative.
