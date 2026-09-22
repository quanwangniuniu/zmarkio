---
paths:
  - "frontend/**/*.{ts,tsx}"
  - "variations-studio-api/**/*.ts"
---

# Frontend architecture & layering

Principle: **thin edges, fat core** — the frontend mirror of the backend rule. Pages and
components stay thin; data-fetching, state, and logic live in `frontend/src/lib/` (API modules,
Zustand stores) and `frontend/src/hooks/`.

## Where code goes (`frontend/src/`)

- **`app/**/page.tsx`** — compose only: read params, call hooks/stores/APIs, render feature
  components, handle route-level loading/error. DON'T put feature logic, fetch orchestration, or
  ad-hoc `axios` calls in a page.
- **`components/<feature>/`** — feature UI, one folder per domain (kebab-case, e.g.
  `email-draft-v2/`). Shared building blocks live in `components/ui` (shadcn primitives),
  `components/common`, and `components/layout` (shell chrome — reuse it, don't rebuild it).
- **`lib/api/<domain>Api.ts`** — ALL HTTP for a domain (see `.claude/rules/frontend-api-conventions.md`).
- **`lib/<domain>Store.ts`** — Zustand state; **`hooks/useX.ts`** — shared hooks (see
  `.claude/rules/frontend-state.md`).
- **`types/<domain>.ts`** — shared TypeScript types (see `.claude/rules/frontend-components.md`).

## Client vs server components

- `'use client'` is the norm for interactive components and pages — most of this codebase is
  client components; add the directive when you use state, effects, refs, or browser APIs.
- Keep `'use client'` OFF route-group `layout.tsx` where server-only APIs belong (`cookies()`
  from `next/headers`, `Suspense`). Reference: `src/app/(project)/layout.tsx`.

## Legacy reality

- New files are `.ts` / `.tsx`. Legacy `.js` exists (e.g. `src/app/layout.js`, a few
  `ui/*.js`) — don't convert it casually; convert only when the task needs it.
- `-v2` feature folders (`tasks-v2`, `messages-v2`) are active rewrites — when working inside
  one, match its patterns rather than the older sibling folder's.

## variations-studio-api

Separate Next.js service (Prisma, validates Django-minted JWTs). It does NOT import from
`frontend/`; keep its logic server-side in route handlers + `lib/` + `src/`. Its own conventions
live in `.claude/rules/variations-studio-api.md`.

## Related rules

- `.claude/rules/frontend-api-conventions.md`, `.claude/rules/frontend-state.md`,
  `.claude/rules/frontend-components.md`, `.claude/rules/code-style.md`,
  `.claude/rules/testing.md`.
