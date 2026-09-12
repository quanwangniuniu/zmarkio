---
paths:
  - "backend/**/*.py"
---

# API conventions (DRF)

## Layering

Views stay thin: **parse input → check permissions → call a service → return `Response`.**
Business logic lives in `backend/<app>/services.py` (module-level functions, `@transaction.atomic`
on multi-write flows). Serializers validate and shape data only — no side effects. Most apps
follow `views.py` + `serializers.py` + `services.py` + `urls.py`; prefer that before inventing
a new layer.

## URLs

- Mounted per app under `/api/<app>/` in `backend/backend/urls.py` (e.g. `path('api/csm/',
  include('csm.urls'))`). A few apps mount at bare `/api/`. **There is no global `/api/v1/`** —
  only `linear_integration` and `zoom_integration` use a `v1` prefix.
- `APPEND_SLASH = True` — every route has a **trailing slash**.
- Within an app: DRF `DefaultRouter` for CRUD + explicit `path(...)` with
  `ViewSet.as_view({'get': 'list', ...})` for extra/nested routes.
- Collection segments are **kebab-case plural** (`customer-users`, `ticket-forms`,
  `sla-policy`). Nested resources use path params (`projects/<int:project_id>/queues/`); many
  list/create endpoints scope by a `?project=<id-or-slug>` query param via
  `core.viewset_mixins.ProjectScopedViewSetMixin`.
- Newer apps use slug detail lookups (`core.slug_mixins.SlugLookupViewSetMixin`) — numeric pk
  returns 404.

## Auth

- SimpleJWT via `core.authentication.TenantAwareJWTAuthentication` (multi-tenant: user lookups
  forced to the Postgres `public` schema; a custom `auth_token_version` claim enables
  revocation). `Authorization: Bearer <token>`.
- Auth endpoints live under **`/auth/`**, not `/api/` (`login/`, `logout/`, `register/`,
  `token/refresh/`, `me/`, Google OAuth, mock SSO). An org-scoped second token travels in the
  `X-Organization-Token` header. Context headers: `x-user-role`, `x-team-id`.
- **`@method_decorator(csrf_exempt, name='dispatch')` on every JWT `APIView`** (e.g.
  `LoginView`, `RegisterView`) — Django's CSRF middleware activates when a `sessionid` cookie is
  present (e.g. an open Admin session) and a JWT frontend never sends a CSRF token.

## Permissions

- The DRF global default is `AllowAny` — **every view must declare `permission_classes`.**
- Two coexisting styles: newer apps use `permission_classes = [IsAuthenticated, <AppPermission>]`
  + object-level checks in `core/permissions.py` (`IsProjectMember`, `IsProjectOwner`); older
  apps (e.g. `budget_approval`) use header-driven RBAC via `utils.rbac_utils`
  (`has_rbac_permission(user, 'BUDGET_REQUEST', 'EDIT', org, team_id)`), with `is_superuser`
  bypass.
- Enforce auth/authorization early; default to deny.

## Errors

- Only `calendars.*` has a standardised error shape (via
  `calendars.exceptions.calendar_exception_handler`, the configured `EXCEPTION_HANDLER`, which
  deliberately no-ops for non-`calendars` views):
  `{ "error": "VALIDATION_ERROR", "message": "...", "request_id": "<uuid>", "timestamp": "...",
  "details": [ { "field": "...", "reason": "...", "message": "..." } ] }`.
- Everywhere else: raise DRF `rest_framework.exceptions.ValidationError({"field": "msg"})` for
  validation, `PermissionDenied` for authz. A common ad-hoc pattern is
  `Response({'error': str(e)}, status=...)`. Prefer the DRF exceptions for new code.
- **Never leak stack traces or internals** in a response.

## Performance

- Avoid N+1: `select_related` / `prefetch_related`. `transaction.atomic()` for multi-write.

## OpenAPI

- No `drf-spectacular` / runtime schema. Specs in `openapi/openapi_spec/*.yaml` are
  **hand-maintained** — update the matching spec file when you change an endpoint's contract.
