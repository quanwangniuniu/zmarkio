---
paths:
  - "frontend/**/*.{ts,tsx}"
---

# Frontend API conventions

Principle: all HTTP goes through the **shared Axios singleton** and **one module per domain**.
Feature components and pages never call `axios` directly.

## The Axios singleton

- Import the default instance: `import api from '@/lib/api'` (`src/lib/api.ts`). **Never**
  `axios.create(...)` in feature code — that bypasses auth and error handling.
- Interceptors already handle, once and centrally: `Authorization: Bearer <token>`,
  `X-Organization-Token` and related tenant headers, 401 → shared token refresh + single retry,
  password-rotation redirect, quota errors, and stripping `Content-Type` for `FormData`. DON'T
  re-implement any of these per call.

## Domain modules (`src/lib/api/<domain>Api.ts`)

Canonical shape for **new** code:

```ts
import api from '@/lib/api';

const TASKS = '/api/tasks/'; // paths are /api/..., always trailing-slash (DRF)

export const taskApi = {
  list: async (params?: TaskListParams) => (await api.get(TASKS, { params })).data,
  create: async (payload: TaskCreatePayload) => (await api.post(TASKS, payload)).data,
};
```

- Export a **camelCase object** named `<domain>Api`; methods are `async` and **return
  `response.data`** (not the raw Axios response).
- Endpoint paths are `/api/...` with **trailing slashes**.
- When editing an existing module, **match that file's style** — some use a `PascalCaseAPI`
  export and/or return the raw Axios response. Don't mix both styles in one module.

## Errors

- Surface failures with `getApiErrorDetail(error, fallback)` from
  `src/lib/api/errorMessage.ts` at the **call site** (hook/component), not inside the API module.
  It unwraps DRF shapes (`detail`, `message`, `non_field_errors`).
- DON'T swallow errors or invent ad-hoc `{ error: ... }` shapes.

## Pagination

- Paged DRF endpoints return `{ results, next, count }`. To fetch all pages, walk
  `data.results` / `data.next` in a loop with a page-count safety cap. Reference:
  `TaskAPI.getAllTasks` in `src/lib/api/taskApi.ts`.

## Related rules

- `.claude/rules/frontend-state.md` (who calls these modules),
  `.claude/rules/code-style.md` (`@/` alias, snake_case fields), `.claude/rules/testing.md`
  (mock `@/lib/api/<domain>Api`).
