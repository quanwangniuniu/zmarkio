---
paths:
  - "backend/**/*.py"
---

# Permissions & security

Default to deny; enforce auth and authorization early. New views use the modern permission stack —
header-driven RBAC is legacy and must not spread.

## View permissions

- DRF's global default is `AllowAny`. **Every view MUST set `permission_classes`** — there is no
  safe default, so an unset one silently exposes the endpoint.
- **Modern (all new views):** `permission_classes = [IsAuthenticated, <AppPermission>]` plus
  object-level checks from `core/permissions.py` (`IsProjectMember`, `IsProjectOwner`). Put the
  authorization decision as early as possible in the request path.
- **Legacy — never add to new views:** header-driven RBAC via `utils.rbac_utils`
  (`has_rbac_permission(user, 'BUDGET_REQUEST', 'EDIT', org, team_id)`) driven by `x-user-role` /
  `x-team-id` headers, with an `is_superuser` bypass. Some older apps (e.g. `budget_approval`) still
  use it; match the file when editing there, but **never newly introduce** it.
- DON'T: leak stack traces or internals in a response. DON'T: use a bare `except Exception` that
  returns a 500 with `str(e)`.

## CSRF on JWT views

- **`@method_decorator(csrf_exempt, name='dispatch')` on every JWT `APIView`** (e.g. `LoginView`,
  `RegisterView`). Django's CSRF middleware activates whenever a `sessionid` cookie is present (an
  open Django Admin session in the same browser), and a JWT frontend never sends a CSRF token — so
  the POST 403s without this.

## Secrets & config

- Read all config through `python-decouple`: `config('KEY', default=..., cast=...)`. Do NOT add a
  split settings package — there is one `backend/backend/settings.py`.
- Secrets needing at-rest encryption use `FIELD_ENCRYPTION_KEYS` — a rotating `key_id:fernet` list
  whose **first entry is active**. Rotate by prepending a new key, never by editing existing entries.
- When you add a new sensitive key name (token, secret, password field), register it in
  `core/services/log_redaction.py` so it is scrubbed from logs.
- Never commit secrets. `.mcp.json` and committed config read from env vars / OAuth, not literals.

## Related rules

- Auth details (SimpleJWT, `TenantAwareJWTAuthentication`, `/auth/` endpoints, token revocation) and
  error shapes: `.claude/rules/api-conventions.md`.
- Tenant isolation (schema-per-org) is a security boundary: `.claude/rules/multi-tenancy.md`.
