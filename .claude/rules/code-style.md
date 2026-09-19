# Code style & naming

There is **no autoformatter or linter for the backend**, and the frontend ESLint config is
just `next/core-web-vitals` with no custom rules. So: **match the style of the file you are
editing.** The points below are the de-facto conventions.

## Python (`backend/`)

- 4-space indent; single quotes for strings (`'name'`, not `"name"`) unless the string contains
  a single quote.
- Imports grouped and blank-line separated: `__future__` → stdlib → Django / DRF / third-party
  → first-party (`from core...`, `from .models import (...)`). Multi-line imports use
  parenthesised lists.
- Type hints are optional and inconsistent in the codebase. When you do type, add
  `from __future__ import annotations` and use PEP 604 unions (`str | None`).
- Keyword-only arguments are favoured for service functions:
  `def create_work_type(project_id, *, name, sort_order=None):`.
- Docstrings: plain triple-double-quoted prose (not Google/NumPy/Sphinx sectioned). Module
  docstrings are common and often cite the Jira ticket (`"""Work type CRUD for CSM-S01-08."""`).
- Private helpers are `_`-prefixed (`_assert_unique_name`).
- Function-local imports are used to break circular imports — keep that pattern where you see it.

## TypeScript / JavaScript (`frontend/`, `variations-studio-api/`)

- 2-space indent, single quotes, semicolons, trailing commas in multiline literals.
- Import with the `@/` alias (`@/lib/api/taskApi`), not deep relative paths (`../../../..`).
- `tsconfig.json` is `strict: true`; `allowJs: true` and some legacy `.js` files still exist —
  new code is `.ts` / `.tsx`.
- Components stay small: split anything past ~200–300 lines into feature components
  (`src/components/<feature>/`) and hooks (`src/hooks/`).
- Pages (`src/app/**/page.tsx`) compose only — read params, call hooks/APIs/stores, render
  feature components, handle route-level loading/error.
- Styling is **Tailwind + Radix only**; reuse `src/components/layout/*` for shell chrome.
- API response fields stay `snake_case` in TS types (they mirror Django).

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Django app dir | `snake_case` | `budget_approval/`, `client_communication/` |
| Model | `PascalCase`, singular | `BudgetRequest`, `QueueAgent` |
| Serializer | `<Model>Serializer` (+ purpose suffix), explicit `fields = [...]`, never `'__all__'` | `TicketFormCreateSerializer` |
| ViewSet / View | `<Resource>ViewSet` (router) / `<Action>View` (APIView) | `QueueViewSet`, `LoginView` |
| Service function | verb-first, module-level in `services.py` | `reorder_work_types` |
| URL name | kebab-case | `name='project-queues'` |
| React component file | `PascalCase.tsx` | `ConversationComposer.tsx` |
| Component folder | `kebab-case` | `src/components/email-draft-v2/` |
| Hook file | `useCamelCase.ts`, one per file | `useChatWebSocket.ts` |
| Zustand store | `camelCase` + `Store` suffix | `src/lib/authStore.ts` |
| API client module | `src/lib/api/<domain>Api.ts` | `csmConversationApi.ts` |
