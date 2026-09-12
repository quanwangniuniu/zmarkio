# Decision Service

Standalone backend-only .NET service for extracting the Decision module from the Django monolith.

This increment establishes the service boundary, Docker runtime, JWT validation, project-level permission checks, and Postgres-backed Decision data access. Local standalone runs can still use in-memory storage, while the dev compose runtime uses Postgres so the service can preserve the current Django Decision API behavior against the existing database tables.

## Local Run

```bash
dotnet run --project services/decision-service/DecisionService.csproj
```

Health check:

```bash
curl http://localhost:5198/health
```

Docker Compose service:

```bash
docker compose -f docker-compose.dev.yml --env-file .env up decision-service
```

Health check through Docker:

```bash
curl http://localhost:8085/health
```

Through the local nginx/API gateway:

```bash
curl http://localhost/api/decisions/
```

## Runtime Configuration

- `DECISION_SERVICE_STORAGE=postgres` enables database-backed storage.
- `DECISION_DB_CONNECTION_STRING` or `ConnectionStrings__DecisionDb` points the service at Postgres.
- `DJANGO_JWT_SIGNING_KEY` or `SECRET_KEY` lets the service validate the existing Django-issued JWT access tokens during this migration phase.

The service validates the JWT itself and checks Decision project membership directly from the database. Django no longer owns the Decision API routes in the local gateway path.

## Initial API Boundary

- `GET /health`
- `GET /api/decisions`
- `GET /api/decisions/{id}`
- `PATCH /api/decisions/{id}`
- `POST /api/decisions/{id}/commit`
- `POST /api/decisions/{id}/approve`
- `POST /api/decisions/{id}/mark_reviewed`
- `POST /api/decisions/{id}/archive`
- `POST /api/decisions/drafts`
- `GET /api/decisions/drafts/{id}`
- `PATCH /api/decisions/drafts/{id}`

The DTO names and JSON fields mirror the current Django Decision API where possible so the frontend can be migrated behind a backend adapter without a UI rewrite.
