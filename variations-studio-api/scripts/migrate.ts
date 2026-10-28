import { join } from 'node:path';

import { runMigrations } from '@/lib/migrate';
import { prisma } from '@/lib/prisma';
import { slugToSchemaName } from '@/lib/tenant';

async function main(): Promise<void> {
  const [command, ...extra] = process.argv.slice(2);
  if (extra.length || (command !== 'apply' && command !== 'status' && command !== 'dry-run')) {
    throw new Error('Expected exactly one command: apply, status or dry-run');
  }
  try {
    const organizations = await prisma.organization.findMany({
      where: { isActive: true }, orderBy: { slug: 'asc' }, select: { slug: true },
    });
    const report = await runMigrations({
      command,
      schemas: organizations.map(org => slugToSchemaName(org.slug)),
      migrationsDir: join(__dirname, '..', 'migrations'),
    });
    console.log(JSON.stringify(report, null, 2));
    if (!report.ok) process.exitCode = 1;
  } finally { await prisma.$disconnect(); }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : 'Migration command failed');
  process.exitCode = 1;
});
