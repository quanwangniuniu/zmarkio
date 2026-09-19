---
description: Review a diff (working tree, a branch, or a PR) against MediaJira conventions
argument-hint: "[PR number | branch | path | JIRA-KEY]  (optional — defaults to the current branch vs prod-preview)"
---

# /review

Review code for correctness and MediaJira conventions. Report findings; **do not edit or commit.**

## Scope

`$ARGUMENTS` (optional) narrows the review:
- a number → `gh pr diff <n>` (and `gh pr view <n>` for context)
- a branch name → `git diff <branch>...HEAD`
- a path → limit the review to that file/dir
- a Jira key or free text → treat as focus notes

With no argument, review `git diff origin/prod-preview...HEAD` (fetch first) plus any uncommitted
changes (`git status`, `git diff`).

This command is for **feature-branch → `prod-preview`** changes. `prod-preview` → `main`
promotion PRs are the release owner's concern — don't review or open those.

## Steps

1. Gather the diff and enough surrounding context to judge it (read the touched files, not just
   the hunks).
2. Apply the checklist below.
3. For anything non-trivial, delegate a second pass to the `code-reviewer` subagent so a fresh
   context re-checks the diff.
4. Report findings grouped **Blocking / Should-fix / Nit**, each with `file:line` and a concrete
   fix. If it's clean, say so.

## Checklist

**Correctness** — does what it claims; edge cases handled; error handling present.

**Backend**
- View is thin; logic is in `services.py`; serializer has no side effects.
- `permission_classes` is set explicitly (global default is `AllowAny`).
- JWT `APIView`s carry `@method_decorator(csrf_exempt, name='dispatch')`.
- No N+1 — `select_related` / `prefetch_related`; `transaction.atomic()` for multi-write.
- New/changed endpoint → matching `openapi/openapi_spec/*.yaml` updated.
- New migration files committed; no existing migration deleted or renamed.
- Errors raised as DRF `ValidationError` / `PermissionDenied`; no stack traces in responses.

**Frontend**
- All HTTP in `src/lib/api/*` — no `axios` in components/pages.
- `@/` imports; components ≤ ~200–300 lines or split; pages compose only.
- Typed API results; shared types in `src/types`. Zustand stores domain-scoped. Tailwind + Radix only.

**Tests** — added/updated for the change; gated-coverage files stay green; no `testpaths` added
to `pytest.ini`.

**Security** — no secrets in code/logs; client input validated server-side; permissions default
to deny; CORS stays an allowlist.

**Style** — matches surrounding code (repo has no backend linter). Flag only clear deviations.
