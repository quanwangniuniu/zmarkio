---
name: deploy
description: How MediaJira ships to production, and where a developer's responsibility ends. Use when asked to deploy, release, ship, promote to main, cut a release, or roll back.
disable-model-invocation: true
---

# Deploying MediaJira

## A developer's responsibility ends at `prod-preview`

The only branch flow a developer (or Claude) drives is **feature branch → reviewed PR → merged
into `prod-preview`**. That's it.

**The `prod-preview` → `main` promotion is not a developer task.** It is owned by whoever runs
releases. Claude must **not**:

- open, draft, or prepare a `prod-preview` → `main` PR,
- merge to `main`, push to `main`, or trigger the deploy webhook,
- run `recompose.sh` or any production compose command.

When someone asks Claude to "deploy" or "release", the answer is: make sure the change is
reviewed and merged into `prod-preview`, then hand off to the release owner. Everything below is
**context only** — so Claude can answer questions, not so it can act.

## What happens after `prod-preview` (for reference)

1. The release owner opens `prod-preview` → `main` (the only branch pair CI's `block` job lets
   PR into `main`).
2. Merging to `main` runs `.github/workflows/mediajira-ci.yml`:
   `block → migration_guard → code_scan → image_scan → application_test → deploy`.
3. The `deploy` job POSTs the deploy webhook, then polls `https://zmarkio.com` for health
   (~45 attempts, 20s apart) and fails the run if it never recovers.
4. Production **database migrations are applied separately** (see `backend/MIGRATIONS.md`) —
   the pipeline only checks migration files are committed and applies them to the CI database.

See `.claude/skills/deploy/deploy-config.md` for job-by-job detail and secret names.

## If a developer asks "how do I get my change to production?"

- Confirm it's merged into `prod-preview` via a reviewed PR (use `/review`).
- If it adds migrations, flag that in the PR description so the release owner coordinates the
  prod migration.
- Then it's out of your hands — the release owner promotes `prod-preview` to `main` on their
  cadence.
