import { main } from '@/scripts/migrate';
import { runMigrations } from '@/lib/migrate';
import { prisma } from '@/lib/prisma';

jest.mock('@/lib/migrate', () => ({ runMigrations: jest.fn() }));
jest.mock('@/lib/prisma', () => ({ prisma: {
  organization: { findMany: jest.fn() },
  $disconnect: jest.fn(),
} }));

const enumerate = prisma.organization.findMany as jest.Mock;
const migrate = runMigrations as jest.Mock;
const originalExitCode = process.exitCode;

beforeEach(() => {
  jest.clearAllMocks();
  process.exitCode = originalExitCode;
  jest.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
  process.exitCode = originalExitCode;
  jest.restoreAllMocks();
});

it('includes registered inactive organizations in the migration targets', async () => {
  enumerate.mockResolvedValue([{ slug: 'active-org' }, { slug: 'inactive-org' }]);
  migrate.mockResolvedValue({ ok: true, results: [] });

  await main(['apply']);

  expect(enumerate).toHaveBeenCalledWith({ orderBy: { slug: 'asc' }, select: { slug: true } });
  expect(migrate).toHaveBeenCalledWith(expect.objectContaining({
    command: 'apply', schemas: ['org_active_org', 'org_inactive_org'],
  }));
  expect(prisma.$disconnect).toHaveBeenCalledTimes(1);
});

it('keeps a failed one-shot migration nonzero for CI', async () => {
  enumerate.mockResolvedValue([{ slug: 'bad-org' }]);
  migrate.mockResolvedValue({ ok: false, results: [{ schema: 'org_bad_org', status: 'failed' }] });

  await main(['apply']);

  expect(process.exitCode).toBe(1);
  expect(prisma.$disconnect).toHaveBeenCalledTimes(1);
});
