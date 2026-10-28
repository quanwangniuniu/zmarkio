import { randomUUID } from 'node:crypto';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Client, escapeIdentifier } from 'pg';

import { runMigrations, type MigrationCommand } from '@/lib/migrate';

const fixtures = (name: string) => path.join(__dirname, 'fixtures/migrations', name);
const connectionString = process.env.DATABASE_URL;
const observer = new Client({ connectionString });
const ownedSchemas: string[] = [];
const ownedDirectories: string[] = [];
const table = (schema: string, name: string) => `${escapeIdentifier(schema)}.${escapeIdentifier(name)}`;

beforeAll(async () => {
  if (!connectionString) throw new Error('Migration tests require DATABASE_URL.');
  await observer.connect();
});

afterAll(async () => {
  try {
    for (const name of ownedSchemas) {
      await observer.query(`DROP SCHEMA IF EXISTS ${escapeIdentifier(name)} CASCADE`);
    }
    for (const directory of ownedDirectories) {
      await rm(directory, { recursive: true, force: true });
    }
  } finally { await observer.end(); }
});

async function createSchema(name = `org_migrate_test_${randomUUID().replace(/-/g, '')}`): Promise<string> {
  await observer.query(`CREATE SCHEMA ${escapeIdentifier(name)}`);
  ownedSchemas.push(name);
  return name;
}

async function temporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(path.resolve(tmpdir()), 'studio-migrations-test-'));
  ownedDirectories.push(directory);
  return directory;
}

async function copyMigrations(): Promise<string> {
  const directory = await temporaryDirectory();
  await cp(fixtures('success'), directory, { recursive: true });
  return directory;
}

function run(
  schemas: string[],
  command: MigrationCommand = 'apply',
  migrationsDir = fixtures('success'),
  timeouts: { lockTimeoutMs?: number; statementTimeoutMs?: number } = {},
) {
  return runMigrations({ command, schemas, migrationsDir, ...timeouts });
}

async function expectEmpty(schema: string) {
  const result = await observer.query(
    'SELECT relname FROM pg_class WHERE relnamespace = $1::regnamespace', [schema],
  );
  expect(result.rows).toEqual([]);
}

async function ledger(schema: string) {
  return (await observer.query(
    `SELECT version, checksum, applied_at FROM ${table(schema, 'studio_migrations')} ORDER BY version`,
  )).rows;
}

it('applies ordered versions with gaps, skips repeats, and applies only new files', async () => {
  const schema = await createSchema();
  const first = await run([schema]);
  expect(first.ok).toBe(true);
  expect(first.results[0]).toMatchObject({
    schema, status: 'applied', appliedBefore: [],
    pending: ['001_create_probe.sql', '003_add_note.sql'], appliedNow: ['001', '003'],
  });
  expect((await observer.query(`SELECT * FROM ${table(schema, 'migrate_probe')}`)).rows)
    .toEqual([{ id: 1, label: 'created', note: 'updated' }]);
  const before = await ledger(schema);
  const again = await run([schema]);
  expect(again.ok).toBe(true);
  expect(again.results[0]).toMatchObject({
    status: 'up_to_date', appliedBefore: ['001', '003'], pending: [], appliedNow: [],
  });
  expect(await ledger(schema)).toEqual(before);
  const directory = await copyMigrations();
  await writeFile(path.join(directory, '004_update_note.sql'), "UPDATE migrate_probe SET note = 'incremental';\n");
  const updated = await run([schema], 'apply', directory);
  expect(updated.ok).toBe(true);
  expect(updated.results[0]).toMatchObject({
    status: 'applied', appliedBefore: ['001', '003'], pending: ['004_update_note.sql'], appliedNow: ['004'],
  });
  expect((await ledger(schema)).slice(0, 2)).toEqual(before);
  expect((await observer.query(`SELECT note FROM ${table(schema, 'migrate_probe')}`)).rows)
    .toEqual([{ note: 'incremental' }]);
});

it.each(['status', 'dry-run'] as const)('%s reads the plan without creating a ledger', async command => {
  const schema = await createSchema();
  const result = await run([schema], command);
  expect(result.ok).toBe(true);
  expect(result.results[0]).toMatchObject({
    status: 'pending', appliedBefore: [], appliedNow: [],
    pending: ['001_create_probe.sql', '003_add_note.sql'],
  });
  if (command === 'dry-run') {
    expect(result.results[0].plannedSql).toEqual([
      { file: '001_create_probe.sql', sql: expect.stringContaining('CREATE TABLE migrate_probe') },
      { file: '003_add_note.sql', sql: expect.stringContaining('ALTER TABLE migrate_probe') },
    ]);
  } else {
    expect(result.results[0]).not.toHaveProperty('plannedSql');
  }
  await expectEmpty(schema);
  await run([schema]);
  const before = await ledger(schema);
  const current = (await run([schema], command)).results[0];
  expect(current.status).toBe('up_to_date');
  if (command === 'dry-run') expect(current.plannedSql).toEqual([]);
  expect(await ledger(schema)).toEqual(before);
});

it('does not create a ledger when the migration directory is empty', async () => {
  const schema = await createSchema();
  const directory = await temporaryDirectory();
  const result = await run([schema], 'apply', directory);
  expect(result.ok).toBe(true);
  expect(result.results[0]).toMatchObject({
    status: 'up_to_date', appliedBefore: [], pending: [], appliedNow: [],
  });
  await expectEmpty(schema);
});

it('reports a missing schema as a skip and does not create it', async () => {
  const schema = `org_migrate_test_${randomUUID().replace(/-/g, '')}`;
  const result = await run([schema]);
  expect(result.ok).toBe(true);
  expect(result.results[0]).toMatchObject({
    schema, status: 'skipped_missing_schema', appliedNow: [],
  });
  expect((await observer.query('SELECT to_regnamespace($1) AS schema', [schema])).rows[0].schema).toBeNull();
});

it('uses PostgreSQL identifier truncation while preserving the schema letter case', async () => {
  const schema = await createSchema(`org_Migrate_Test_${randomUUID().replace(/-/g, '')}`.padEnd(63, 'x'));
  const target = `${schema}x`;
  const result = await run([target]);
  expect(result.ok).toBe(true);
  expect(result.results[0]).toMatchObject({
    schema: target, status: 'applied', appliedNow: ['001', '003'],
  });
  expect((await observer.query(`SELECT * FROM ${table(schema, 'migrate_probe')}`)).rows)
    .toEqual([{ id: 1, label: 'created', note: 'updated' }]);
});

it('continues after the middle tenant fails and fills only that tenant on retry', async () => {
  const schemas = [await createSchema(), await createSchema(), await createSchema()];
  await observer.query(`CREATE TABLE ${table(schemas[1], 'migrate_probe')} (id integer PRIMARY KEY, label text)`);
  await observer.query(`INSERT INTO ${table(schemas[1], 'migrate_probe')} VALUES (99, 'existing')`);
  const first = await run(schemas);
  expect(first.ok).toBe(false);
  expect(first.results.map(result => result.status)).toEqual(['applied', 'failed', 'applied']);
  expect(first.results[1]).toMatchObject({
    appliedNow: [], error: { code: '42P07', file: '001_create_probe.sql' },
  });
  expect((await observer.query(`SELECT * FROM ${table(schemas[1], 'migrate_probe')}`)).rows)
    .toEqual([{ id: 99, label: 'existing' }]);
  expect((await observer.query('SELECT to_regclass($1) AS ledger', [table(schemas[1], 'studio_migrations')])).rows[0].ledger)
    .toBeNull();
  const successfulLedger = await ledger(schemas[0]);
  await observer.query(`DROP TABLE ${table(schemas[1], 'migrate_probe')}`);
  const retry = await run(schemas);
  expect(retry.ok).toBe(true);
  expect(retry.results.map(result => result.status)).toEqual(['up_to_date', 'applied', 'up_to_date']);
  expect(retry.results.map(result => result.appliedNow)).toEqual([[], ['001', '003'], []]);
  expect(await ledger(schemas[0])).toEqual(successfulLedger);
});

it.each(['public', 'not_an_org', 'org_bad";DROP SCHEMA public;--'])('rejects target %s before writing any tenant', async invalid => {
  const schema = await createSchema();
  await expect(run([schema, invalid])).rejects.toThrow();
  await expectEmpty(schema);
});

it('rejects malformed or duplicate migration versions before writes', async () => {
  const schema = await createSchema();
  const directory = await copyMigrations();
  await writeFile(path.join(directory, '001_duplicate.sql'), 'SELECT 1;\n');
  await expect(run([schema], 'apply', directory)).rejects.toThrow();
  await rm(path.join(directory, '001_duplicate.sql'));
  await writeFile(path.join(directory, 'bad_name.sql'), 'SELECT 1;\n');
  await expect(run([schema], 'apply', directory)).rejects.toThrow();
  await expectEmpty(schema);
});

it('normalizes CRLF when comparing recorded checksums', async () => {
  const schema = await createSchema();
  await run([schema]);
  const directory = await copyMigrations();
  for (const file of ['001_create_probe.sql', '003_add_note.sql']) {
    const sql = (await readFile(path.join(directory, file), 'utf8')).replace(/\r\n/g, '\n');
    await writeFile(path.join(directory, file), sql.replace(/\n/g, '\r\n'));
  }
  expect((await run([schema], 'apply', directory)).results[0].status).toBe('up_to_date');
});

it.each(['checksum', 'lower-pending-version'])('rejects %s for every command without writes', async kind => {
  const schema = await createSchema();
  await run([schema]);
  const directory = await copyMigrations();
  if (kind === 'checksum') {
    const file = path.join(directory, '001_create_probe.sql');
    await writeFile(file, (await readFile(file, 'utf8')) + '-- changed after application\n');
  } else {
    await writeFile(path.join(directory, '002_late.sql'), "UPDATE migrate_probe SET note = 'late';\n");
  }
  const before = await ledger(schema);
  for (const command of ['status', 'dry-run', 'apply'] as const) {
    const result = await run([schema], command, directory);
    expect(result.ok).toBe(false);
    expect(result.results[0]).toMatchObject({
      status: 'failed', appliedNow: [],
      error: { message: expect.stringContaining(kind === 'checksum' ? 'Checksum mismatch' : 'Cannot apply') },
    });
    expect(await ledger(schema)).toEqual(before);
    expect((await observer.query(`SELECT note FROM ${table(schema, 'migrate_probe')}`)).rows)
      .toEqual([{ note: 'updated' }]);
  }
});

it('preserves unknown history but rejects pending files below its latest version', async () => {
  const schema = await createSchema();
  await run([schema]);
  await observer.query(`INSERT INTO ${table(schema, 'studio_migrations')} (version, checksum) VALUES ('999', $1)`, ['0'.repeat(64)]);
  const before = await ledger(schema);
  const directory = await copyMigrations();
  for (const command of ['status', 'dry-run', 'apply'] as const) {
    const result = await run([schema], command, directory);
    expect(result.ok).toBe(true);
    expect(result.results[0]).toMatchObject({
      status: 'up_to_date', appliedBefore: ['001', '003', '999'], pending: [], appliedNow: [],
    });
    expect(await ledger(schema)).toEqual(before);
  }
  await writeFile(path.join(directory, '004_blocked.sql'), 'CREATE TABLE unknown_history_guard (id integer);\n');
  for (const command of ['status', 'dry-run', 'apply'] as const) {
    const result = await run([schema], command, directory);
    expect(result.ok).toBe(false);
    expect(result.results[0]).toMatchObject({
      status: 'failed', appliedNow: [], error: { message: expect.stringContaining('recorded version 999') },
    });
    expect(await ledger(schema)).toEqual(before);
    expect((await observer.query('SELECT to_regclass($1) AS relation', [table(schema, 'unknown_history_guard')])).rows[0].relation).toBeNull();
  }
});

it('rejects transaction control and rolls back every file and ledger entry', async () => {
  const schema = await createSchema();
  const result = await run([schema], 'apply', fixtures('transaction-control'));
  expect(result.ok).toBe(false);
  expect(result.results[0]).toMatchObject({
    status: 'failed', appliedNow: [], error: { code: '0A000', file: '002_commit.sql' },
  });
  await expectEmpty(schema);
});

it('times out on the shared provisioning lock and succeeds after its release', async () => {
  const schema = await createSchema();
  const holder = new Client({ connectionString });
  await holder.connect();
  try {
    await holder.query('BEGIN');
    await holder.query('SELECT pg_advisory_xact_lock($1, $2)', [0x5A4D4152, 0x54454E54]);
    const result = await run([schema], 'apply', fixtures('success'), { lockTimeoutMs: 150 });
    expect(result.ok).toBe(false);
    expect(result.results[0]).toMatchObject({ status: 'failed', appliedNow: [], error: { code: '55P03' } });
    await expectEmpty(schema);
  } finally {
    try { await holder.query('ROLLBACK'); } finally { await holder.end(); }
  }
  expect((await run([schema])).results[0]).toMatchObject({ status: 'applied', appliedNow: ['001', '003'] });
});

it('rolls back earlier DDL when the migration statement times out', async () => {
  const schema = await createSchema();
  const result = await run([schema], 'apply', fixtures('timeout'), { statementTimeoutMs: 300 });
  expect(result.ok).toBe(false);
  expect(result.results[0]).toMatchObject({
    status: 'failed', appliedNow: [], error: { code: '57014', file: '001_wait.sql' },
  });
  await expectEmpty(schema);
});
