import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Client, escapeIdentifier } from 'pg';

import { TenantStatus } from '@/lib/migrationStatus';
import { assertSchemaName } from '@/lib/tenant';

export type MigrationCommand = 'apply' | 'status' | 'dry-run';
type Migration = { version: string; file: string; sql: string; checksum: string };
type History = { version: string; checksum: string };

type TenantMigrationResult = {
  schema: string;
  status: TenantStatus;
  appliedBefore: string[];
  /** Files pending when this invocation planned the tenant, before execution. */
  pending: string[];
  plannedSql?: Array<{ file: string; sql: string }>;
  appliedNow: string[];
  error?: { file?: string; code?: string; message: string };
};

type MigrationOptions = {
  command: MigrationCommand;
  schemas: string[];
  migrationsDir: string;
  lockTimeoutMs?: number;
  statementTimeoutMs?: number;
};

// Must match backend/core/services/tenant.py lock_tenant_provisioning.
const LOCK_NAMESPACE = 0x5A4D4152;
const LOCK_RESOURCE = 0x54454E54;

async function loadMigrations(directory: string): Promise<Migration[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = entries.filter(entry => entry.isFile() && entry.name.endsWith('.sql'))
    .map(entry => entry.name).sort();
  const versions = new Set<string>();
  const migrations: Migration[] = [];
  for (const file of files) {
    const match = /^(\d{3})_[A-Za-z0-9_-]+\.sql$/.exec(file);
    if (!match) throw new Error(`Migration must use NNN_name.sql: ${file}`);
    const version = match[1];
    if (versions.has(version)) throw new Error(`Duplicate migration version: ${version}`);
    versions.add(version);
    const sql = (await readFile(join(directory, file), 'utf8')).replace(/\r\n/g, '\n');
    migrations.push({ version, file, sql, checksum: createHash('sha256').update(sql).digest('hex') });
  }
  return migrations;
}

function plan(migrations: Migration[], history: History[]): Migration[] {
  const applied = new Map(history.map(row => [row.version, row.checksum]));
  // History is ordered by version; include versions unknown to this checkout.
  const latest = history.length ? history[history.length - 1].version : '';
  for (const migration of migrations) {
    if (applied.has(migration.version) && applied.get(migration.version) !== migration.checksum) {
      throw new Error(`Checksum mismatch for ${migration.file}`);
    }
    if (!applied.has(migration.version) && migration.version <= latest) {
      throw new Error(`Cannot apply ${migration.file} after recorded version ${latest}`);
    }
  }
  return migrations.filter(migration => !applied.has(migration.version));
}

async function runTenant(
  schema: string, migrations: Migration[], options: MigrationOptions, connectionString: string
): Promise<TenantMigrationResult> {
  const result: TenantMigrationResult = {
    schema, status: TenantStatus.Failed, appliedBefore: [], pending: [], appliedNow: [],
  };
  const client = new Client({ connectionString, connectionTimeoutMillis: 10_000 });
  const quotedSchema = escapeIdentifier(schema);
  const ledger = `${quotedSchema}."studio_migrations"`;
  let currentMigration: Migration | undefined;
  let commitAttempted = false;
  // Queries report failures; also consume idle errors so a disconnect cannot crash the process.
  client.on('error', () => {});
  try {
    await client.connect();
    await client.query(options.command === 'apply'
      ? 'BEGIN ISOLATION LEVEL READ COMMITTED'
      : 'BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY');
    await client.query("SELECT set_config('lock_timeout', $1, true), set_config('statement_timeout', $2, true)",
      [`${options.lockTimeoutMs ?? 5000}ms`, `${options.statementTimeoutMs ?? 60000}ms`]);
    if (options.command === 'apply') {
      await client.query('SELECT pg_advisory_xact_lock($1::int4, $2::int4)', [LOCK_NAMESPACE, LOCK_RESOURCE]);
    }
    // Match PG's 63-byte identifier truncation used by Django and tenantTable.
    // Names are ASCII; direct catalog comparison preserves case without regnamespace parsing.
    const namespace = await client.query(
      'SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = left($1, 63)', [schema]);
    if (namespace.rows.length === 0) {
      result.status = TenantStatus.SkippedMissingSchema;
      return result;
    }
    await client.query(`SET LOCAL search_path TO ${quotedSchema}`);
    // For apply, read AFTER acquiring the same lock, in a fresh READ COMMITTED statement.
    // This lets a retry observe a previous uncertain COMMIT once its lock is released.
    const exists = await client.query<{ name: string | null }>('SELECT to_regclass($1)::text AS name', [ledger]);
    const history = exists.rows[0].name === null ? []
      : (await client.query<History>(`SELECT version, checksum FROM ${ledger} ORDER BY version`)).rows;
    result.appliedBefore = history.map(row => row.version);
    const pending = plan(migrations, history);
    result.pending = pending.map(migration => migration.file);
    if (options.command === 'dry-run') result.plannedSql = pending.map(({ file, sql }) => ({ file, sql }));
    if (options.command !== 'apply' || pending.length === 0) {
      result.status = pending.length ? TenantStatus.Pending : TenantStatus.UpToDate;
      return result;
    }
    await client.query(`CREATE TABLE IF NOT EXISTS ${ledger} (
      version text PRIMARY KEY, checksum text NOT NULL,
      applied_at timestamptz NOT NULL DEFAULT now()
    )`);
    // Dynamic EXECUTE runs in a function's atomic context: PG rejects transaction
    // control, including aliases and nested DO/CALL, without a SQL text scanner.
    await client.query(`CREATE FUNCTION pg_temp.studio_run(sql text) RETURNS void
      LANGUAGE plpgsql AS $runner$ BEGIN EXECUTE sql; END $runner$`);
    for (const migration of pending) {
      currentMigration = migration;
      await client.query('SELECT pg_temp.studio_run($1)', [migration.sql]);
      await client.query(`INSERT INTO ${ledger} (version, checksum) VALUES ($1, $2)`,
        [migration.version, migration.checksum]);
    }
    currentMigration = undefined;
    commitAttempted = true;
    await client.query('COMMIT');
    result.appliedNow = pending.map(migration => migration.version);
    result.status = TenantStatus.Applied;
  } catch (error) {
    result.status = commitAttempted ? TenantStatus.CommitUnknown : TenantStatus.Failed;
    result.error = {
      file: currentMigration?.file,
      code: error && typeof error === 'object' && 'code' in error && typeof error.code === 'string'
        ? error.code : undefined,
      message: error instanceof Error ? error.message : 'Unknown migration error',
    };
  } finally {
    // This connection is never reused; closing it rolls back any open transaction.
    await client.end();
  }
  return result;
}

/** Validate shared inputs before opening a connection; isolate failures per tenant. */
export async function runMigrations(options: MigrationOptions): Promise<{
  command: MigrationCommand; ok: boolean; results: TenantMigrationResult[];
}> {
  for (const schema of options.schemas) {
    assertSchemaName(schema);
    if (schema === 'public') throw new Error('public is not a migration target');
  }
  const migrations = await loadMigrations(options.migrationsDir);
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error('DATABASE_URL is required');
  const results: TenantMigrationResult[] = [];
  for (const schema of options.schemas) results.push(await runTenant(schema, migrations, options, connectionString));
  return {
    command: options.command,
    ok: results.every(result => result.status !== TenantStatus.Failed && result.status !== TenantStatus.CommitUnknown),
    results,
  };
}
