import { join } from 'node:path';
import { Client } from 'pg';

import { runMigrations } from '@/lib/migrate';

jest.mock('pg', () => ({ ...jest.requireActual('pg'), Client: jest.fn() }));

const MockClient = Client as jest.MockedClass<typeof Client>;

function connection(commitError?: Error) {
  MockClient.mockImplementationOnce(() => ({
    on: jest.fn(),
    connect: async () => {},
    end: async () => {},
    query: async (sql: string) => {
      if (sql.includes('pg_catalog.pg_namespace')) return { rows: [{}] };
      if (sql.includes('to_regclass')) return { rows: [{ name: null }] };
      if (sql === 'COMMIT' && commitError) throw commitError;
      return { rows: [] };
    },
  }) as unknown as Client);
}

it('reports an uncertain COMMIT and continues to the next tenant', async () => {
  MockClient.mockReset();
  connection(new Error('lost COMMIT response'));
  connection();
  const report = await runMigrations({
    command: 'apply',
    schemas: ['org_uncertain', 'org_next'],
    migrationsDir: join(__dirname, 'fixtures/migrations/success'),
  });
  expect(report.ok).toBe(false);
  expect(report.results[0]).toMatchObject({
    schema: 'org_uncertain', status: 'commit_unknown', appliedNow: [],
    error: { message: 'lost COMMIT response' },
  });
  expect(report.results[1]).toMatchObject({
    schema: 'org_next', status: 'applied', appliedNow: ['001', '003'],
  });
});
