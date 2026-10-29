# Tenant migrations

Install dependencies, generate Prisma Client and set `DATABASE_URL`, then run from this directory's parent:

```sh
npm run migrate             # Apply pending files
npm run migrate:status      # Show applied versions and pending files
npm run migrate:dry-run     # Also include pending SQL in plannedSql; execute nothing
```

- Targets are all registered organizations, including inactive ones, sorted by slug.
  Missing schemas are reported as
  `skipped_missing_schema`; they are not created. `public` is never a target.
- Use unique `NNN_description.sql` files. Numbering gaps are allowed. Never edit or
  renumber applied migrations; known versions must match checksums (CRLF normalized to LF).
- Unknown ledger versions are retained and reported. Pending versions must exceed the
  highest recorded version, including unknown ones; older files cannot be applied late.
  This does not guarantee compatibility across branches. Use a matching development
  database or restore both database and history; never delete ledger rows alone to bypass a conflict.
- Each tenant's pending files and `studio_migrations` ledger share one transaction.
  Do not include transaction control. Use `CREATE TABLE AS`, not `SELECT INTO`.
- SQL is trusted: keep the tenant search_path and runner settings intact; explicitly
  qualify shared references such as `public.core_customuser`.
- Apply holds Django's provisioning lock during each tenant's transaction. Defaults are
  5 seconds for lock waits and 60 seconds per file, with no total transaction deadline.
- JSON reports each tenant; failures do not stop later tenants but cause nonzero exit.
  `pending` is the original plan; `appliedNow` contains confirmed committed versions.
- `commit_unknown` means COMMIT was not confirmed, including connection loss or a
  deferred constraint error. Inspect the error, fix any known cause, then re-run apply
  to reconcile the ledger. It does not mean the transaction was confirmed rolled back.
- Status/dry-run are read-only and do not verify that SQL will execute successfully.

CI runs migrations once before tests and fails on a nonzero exit. An empty SQL
directory creates no ledger.

## Migration service

The migration service runs immediately, then waits `STUDIO_MIGRATION_INTERVAL_SECONDS`
(default 60) after each round, including failures. New organizations are migrated by
a later round. This is not a readiness deadline; failed tenants need repair.
Stop the container to stop the loop and current command.
Studio starts independently and returns 503 until its tenant migrations are ready.

Start both development services from the repository root:

```sh
docker compose -p mediajira-v2 -f docker-compose.dev.yml -f variations-studio-api/compose.dev.yml up -d --build variations-studio-api variations-studio-migrations
```

## Production and preview deployment

Set `STUDIO_MIGRATION_DATABASE_URL` to Django's database with URL-encoded credentials
and permission to create/alter tenant tables. Use a direct or session-pooled connection;
transaction pooling is unsupported. A missing URL fails migration rounds, not Compose
parsing. Start the database/pool first, then run from the repository root using the
deployed compose variant (pro, preview, pro.single or pro.dr):

```sh
docker compose -f docker-compose.pro.yml build variations-studio-migrations
docker compose -f docker-compose.pro.yml run --rm --no-deps variations-studio-migrations npm run migrate
# Continue only after exit 0 and ready tenant schemas; check skipped_missing_schema.
docker compose -f docker-compose.pro.yml up -d --no-deps variations-studio-migrations
docker compose -f docker-compose.pro.yml up -d --build --no-deps backend
docker compose -f docker-compose.pro.yml logs --timestamps variations-studio-migrations
```

For `pro.dr`, prepare the intended `backend_base_image` through the DR release process
and use `up -d --no-deps backend`; that file has no backend build.

## Variation table

- `001_ad_copy_variation_baseline.sql` creates the Django baseline if the table is
  missing, or verifies it before adoption without changing existing data or sequence state.
- If the table already exists, only the identity sequence name and CACHE may differ;
  columns, constraints, indexes and all other sequence settings must match exactly.
- `002_ad_copy_variation_project_cascade.sql` changes only the tenant-local project FK
  to `ON DELETE CASCADE`, retaining its name and deferred checks.
- Creative/user FKs remain `NO ACTION`; direct deletion of referenced parents is rejected.
- Locks taken by these files, including on referenced parent tables, are held until
  the tenant transaction ends.

## Platform and copy fields

`003_add_platform_and_fields.sql` adds indexed `platform` (default `meta`) and nullable
`fields` JSONB without a default, then builds `fields` from the four legacy copy columns
without changing them. Legacy-only writers such as Django can leave `fields` NULL or stale.

For unclaimed Django tables, measure the full 001 → 002 → 003 path with production-sized
data before deployment. Locks on the variation table and 002's referenced `core_project`
persist through 003's index build/backfill until commit. Check each file against the
timeout above; it does not bound total transaction or lock duration.

While Meta content is fully reconstructible from legacy columns and readers use them,
clear/rebuild the derived data in separate higher-version migrations:

```sql
-- Clear Meta fields.
UPDATE ad_copy_variation_adcopyvariation SET fields = NULL WHERE platform = 'meta';

-- Rebuild Meta fields in a later migration.
UPDATE ad_copy_variation_adcopyvariation
SET fields = jsonb_build_object(
    'hook', hook, 'headline', headline, 'description', description, 'cta', cta)
WHERE platform = 'meta' AND fields IS NULL;
```
