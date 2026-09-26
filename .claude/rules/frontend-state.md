---
paths:
  - "frontend/**/*.{ts,tsx}"
---

# Frontend state & data fetching

Principle: the default data-flow is **custom hooks + Zustand + the Axios singleton**.
`@tanstack/react-query` is reserved — only the comments feature uses it, and it is the only
`QueryClientProvider` in the app. DON'T reach for TanStack for a new domain without a specific
reason (heavy cache invalidation across views).

## Zustand stores (`src/lib/<domain>Store.ts`)

- `export const use<Domain>Store = create<<Domain>State>((set, get) => ({ ... }))`. The
  interface lists state fields first, then action signatures. Actions are plain setters plus
  domain mutators.
- Mutations that hit the server use the **optimistic-update guard**: `begin* / resolve* /
  rollback*` keyed by per-operation ids so a stale server response can't clobber newer local
  state. Reference: `src/lib/taskStore.ts`.
- Persisted stores wrap the initializer with `persist(..., { name, storage:
  createJSONStorage(...), partialize, onRehydrateStorage })` — `partialize` persists only what's
  needed, `onRehydrateStorage` sets a `hasHydrated` flag. Reference: `src/lib/authStore.ts`.
- Async auth/domain actions (`login`, `logout`, `initialize*`) live inside the store and call
  the domain API module.

## Hooks (`src/hooks/useX.ts`)

- `'use client'`, one hook per file, `useCamelCase.ts`. Pull state/actions from the store,
  keep local `loading` / `error` via `useState`, wrap API calls in `useCallback`, and normalize
  API responses before returning. Reference: `src/hooks/useTaskData.ts` (canonical).
- The TanStack exception pattern (query-key factory + `useQuery` / `useMutation` with
  invalidate-on-success) lives in `src/hooks/useComments.ts` — follow it only if you're
  extending that feature.

## Related rules

- `.claude/rules/frontend-api-conventions.md` (the modules hooks/stores call),
  `.claude/rules/testing.md` (mock the API module, not the store).
