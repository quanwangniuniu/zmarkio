# Decision Service Boundary

The Decision service owns the HTTP API under `/api/decisions/...`.

## Request Flow

1. Browser and frontend code call `/api/decisions/...`.
2. Nginx maps those requests to `decision-service:8080`.
3. The .NET service validates the existing JWT access token.
4. The .NET service checks project membership and role level from PostgreSQL.
5. The .NET service reads and writes Decision data directly.

## Owned Runtime Responsibilities

- Decision list/detail APIs
- Decision draft create/update APIs
- Decision status transitions: commit, approve, mark reviewed, archive
- Decision soft delete
- Decision signals
- Decision reviews
- Decision graph connections
- Decision authorization for view/edit/approve

## Database Tables Used By The Service

- `decisions`
- `decision_options`
- `decision_signals`
- `decision_edges`
- `decision_reviews`
- `core_project`
- `core_projectmember`
- `core_customuser`

## Django Boundary

Django no longer exposes `decision.urls` through `/api/decisions/...`.

The Decision service does not call a Django internal authorization endpoint. It validates JWTs and checks project membership itself.

The current JWT is still issued by the existing Django authentication system. A fully separate Auth service can replace that later without changing the Decision controllers.

## Future Hardening

- Move Decision schema migrations into a .NET-owned migration runner.
- Replace shared role-level constants with a central Permission service or signed permission policy feed.
- Add production deployment manifests for Azure Container Apps or AKS.
- Add integration tests that run against a temporary PostgreSQL database.
