# Deploy configuration reference

Detail behind `SKILL.md`. Source of truth is `.github/workflows/mediajira-ci.yml`. **No secret
values here — names only.**

**Context only.** The `prod-preview` → `main` promotion and the `deploy` job are the release
owner's domain, not a developer's — Claude does not act on anything in this file.

## Pipeline (`.github/workflows/mediajira-ci.yml`, "MediaJira CI/CD Pipeline")

Triggers: push to `main` / `prod-preview`, tags `v*`, PRs into `main` / `prod-preview`.

| Job | Purpose |
| --- | --- |
| `block` | Branch-flow guard: only `prod-preview` may PR into `main`; `prod-preview` accepts PRs from any branch. |
| `code_scan` | Node 20 `npm ci` + `depcheck` + `npm run build`; Python 3.11 `bandit` + `pip-audit`; `npm audit`; `gitleaks`; Dockerfiles must contain `USER`; Terraform `fmt`/`validate`. |
| `migration_guard` | Fails if a PR deletes/renames `backend/**/migrations/*.py`. Bypass label: `migration-override`. |
| `image_scan` | Build backend + frontend images (ghcr.io cache), Trivy HIGH/CRITICAL scan. |
| `application_test` | `docker compose --profile ci up -d`; `makemigrations --check` + `migrate` with `backend.ci_settings`; `pytest -v` in the backend container; serial excluded tests; frontend `next lint` + `npm run build` + `npm test`; provision tenant schema + JWT fixtures + `variations-studio-api` tests. |
| `deploy` | **push to `main` only, after all above pass.** POST to the deploy webhook, then poll production health. |
| `sbom_release` | On `v*` tags: CycloneDX SBOMs attached to the GitHub Release. |
| `notify` | Discord webhook with per-job status. |

## `deploy` job

```
POST $MEDIAJIRA_DEPLOY_WEBHOOK_URL_MAIN
  Header: X-Deploy-Token: $MEDIAJIRA_DEPLOY_TOKEN
  Body:   {"action": "deploy", "source": "github"}
```

Then health-checks `MEDIAJIRA_PRODUCTION_HEALTH_URL` (default `https://zmarkio.com`):
`MEDIAJIRA_HEALTH_CHECK_ATTEMPTS` (default 45) tries, `MEDIAJIRA_HEALTH_CHECK_INTERVAL`
(default 20) seconds apart; any 2xx/3xx passes, otherwise the run fails.

The webhook receiver on the prod host is `ops/webhook/deploy_listener.py` (gitignored;
operated outside this repo). The `prod-preview` deploy path and an AWS CodeDeploy path exist in
the workflow but are commented out.

## Secret / variable names (GitHub repo settings)

- Secrets: `MEDIAJIRA_DEPLOY_WEBHOOK_URL_MAIN`, `MEDIAJIRA_DEPLOY_TOKEN`,
  (unused/commented: `MEDIAJIRA_DEPLOY_WEBHOOK_URL_PREVIEW`, `AWS_*`).
- Vars: `MEDIAJIRA_PRODUCTION_HEALTH_URL`, `MEDIAJIRA_HEALTH_CHECK_ATTEMPTS`,
  `MEDIAJIRA_HEALTH_CHECK_INTERVAL`.

## Production compose files

`docker-compose.pro.yml`, `docker-compose.pro.single.yml` (single-host, used by `recompose.sh`),
`docker-compose.pro.dr.yml` (disaster recovery). `docker-compose.preview.yml` for prod-preview.
None of these are edited by tooling.
