---
paths:
  - "backend/**/*.py"
  - "frontend/**/*.{ts,tsx}"
  - "variations-studio-api/**/*.ts"
---

# Testing

Add or update tests for any behaviour change or bug fix. Don't lower coverage casually.

## Backend (`backend/`)

- Run tests **inside the container**, with the stack up:
  - Everyday: `docker compose -f docker-compose.dev.yml exec backend python manage.py test [<app>]`
  - Full suite / matches CI: `docker compose -f docker-compose.dev.yml exec backend pytest`
  - Single test: `... exec backend pytest backend/<app>/tests/test_views.py::TestX::test_y`
- **Do not add `testpaths` to `backend/pytest.ini`** — there is a warning comment explaining a
  past regression that silently cut CI from ~4,500 to ~1,100 tests.
- Runner is `pytest` + `pytest-django` (`--ds=backend.settings`), parallel (`-n auto
  --dist loadscope`), `asyncio_mode=auto`, `--reruns 2`. CI runs against **real PostgreSQL**.
- Coverage gate: `--cov-fail-under=85`, scoped to `budget_approval`, `retrospective`,
  `campaign`, `client_communication`, `meetings`. Don't drop below it for those apps.
- Test location: `backend/<app>/tests/test_*.py`. Classes `Test*`, functions `test_*`.
  `pytest.ini` also discovers `tests.py` and `*_tests.py`.
- Fixtures, not factories: shared fixtures in `backend/conftest.py` (`api_client`,
  `organization`, `user`, `project`, `member_client`, …) and per-app `tests/conftest.py`.
  There is **no `factory_boy`** — build objects with `Model.objects.create(...)`.
- What is real vs mocked:
  - **DB: real** (`@pytest.mark.django_db` / `TransactionTestCase`); tenant schema provisioning
    actually runs.
  - **External APIs (Anthropic, Google, Stripe, Slack, Meta, …): mocked** with `unittest.mock`
    / `pytest-mock` (`mocker.patch`). No VCR/cassette library.
  - **Celery: not eager** — patch the task at its call site.
  - Time: `freezegun` (`@freeze_time(...)`). Fake data: `faker`.
- Markers available: `unit, integration, concurrency, escalation, permissions, slow, api,
  models, services, views, asyncio`.
- Never delete or rename an existing migration file (CI `migration_guard` blocks it).

## Frontend (`frontend/`)

- `docker compose -f docker-compose.dev.yml exec frontend npm test` (Jest via `next/jest` +
  React Testing Library). CI: `npm run test:ci`. Lint first: `npm run lint`.
- Test location: `src/**/__tests__/**` or colocated `src/<area>/__tests__/*.test.ts`; files
  `<Name>.test.tsx` / `.test.ts`.
- Mock the API module directly with `jest.mock('@/lib/api/<domain>Api', ...)` returning
  `jest.fn()`s. **MSW is installed but Storybook-only** — do not reach for `setupServer` in Jest.
- Global coverage threshold is 0; a few specific files/globs are gated ~85% in `jest.config.js`
  — keep those green.
- E2E: Playwright is primary (`frontend/e2e/`, `npm run test:e2e`); Cypress is secondary.

## variations-studio-api

- Jest (`npm test`), preceded by `npx prisma generate && npx tsc --noEmit` (matches CI).
