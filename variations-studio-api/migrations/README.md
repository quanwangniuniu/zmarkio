# Tenant migrations

Install dependencies, generate Prisma Client and set `DATABASE_URL`, then run from this directory's parent:

```sh
npm run migrate             # Apply pending files
npm run migrate:status      # Show applied versions and pending files
npm run migrate:dry-run     # Also include pending SQL in plannedSql; execute nothing
```

- Targets are active organizations, sorted by slug. Missing schemas are reported as
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

Development compose runs migrations before Studio starts; CI runs them before tests.
An empty SQL directory creates no ledger.
