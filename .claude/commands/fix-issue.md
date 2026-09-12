---
description: Take a GitHub issue or Jira ticket from diagnosis to a review-ready change
argument-hint: "<issue number | JIRA-KEY>  (+ optional notes)"
---

# /fix-issue

Analyse and fix the issue in `$ARGUMENTS`, then hand a review-ready change back to the human.

**Claude cannot stage, commit, or push** (blocked in `.claude/settings.json`). Stop at a clean
working tree with a proposed commit message and PR text; the developer runs the git commands.

## Steps

1. **Understand the issue.**
   - Pure number → `gh issue view <n>` (and its comments).
   - Jira key (`SMP-…`, `MED-…`, `BGF-…`, `DX-…`) → ask the user to paste the ticket description
     if it isn't in the message; restate your understanding before coding.
2. **Explore.** Find the relevant code (grep/glob, read the files). Identify root cause, not
   just the symptom.
3. **Plan** briefly if the change spans multiple files or the approach is uncertain — otherwise
   go.
4. **Implement** the smallest correct fix, following `@.claude/rules/code-style.md`,
   `@.claude/rules/api-conventions.md`, and the repo's `thin edges, fat core` pattern.
5. **Test.**
   - Write a failing test that reproduces the bug first where practical.
   - Backend: `docker compose -f docker-compose.dev.yml exec backend python manage.py test <app>`
     (and `pytest` for the touched area).
   - Frontend: `docker compose -f docker-compose.dev.yml exec frontend npm run lint && npm test`.
   - If a model changed: `python manage.py makemigrations <app>` and commit the migration.
   - Update `openapi/openapi_spec/*.yaml` if an API contract changed.
6. **Summarise for the human:**
   - The diff and what it does.
   - A Conventional Commit message with the Jira key, e.g.
     `fix(budget_approval): reject negative pool top-ups (MED-412)`.
   - A PR description (context, change, testing, breaking changes) with the issue/Jira link.
     **Base branch is always `prod-preview`, never `main`.**
   - The exact `git add … && git commit … && git push …` commands for the developer to run.
