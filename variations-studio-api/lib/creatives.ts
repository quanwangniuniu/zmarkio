import { ApiError } from '@/lib/bulk';
import { prisma } from '@/lib/prisma';
import type { CopyJson } from '@/lib/prompts';

export function parseCreativeId(raw: unknown): bigint {
  if (raw === undefined || raw === null || raw === '') {
    throw new ApiError(400, 'creative_id required for source_mode=existing');
  }
  if (typeof raw === 'number' && Number.isInteger(raw) && raw > 0) {
    return BigInt(raw);
  }
  if (typeof raw === 'string' && /^\d+$/.test(raw.trim())) {
    return BigInt(raw.trim());
  }
  throw new ApiError(400, 'creative_id required for source_mode=existing');
}

export function creativeToTemplate(row: {
  title: string;
  body: string;
  callToActionType: string;
}): CopyJson {
  const body = row.body || '';
  const hook = body.split('\n')[0] || '';
  return {
    hook,
    headline: row.title || '',
    description: body,
    cta: row.callToActionType || '',
  };
}

export async function loadCreativeForProject(
  creativeId: bigint,
  projectId: bigint
) {
  const creative = await prisma.metaAdCreative.findFirst({
    where: { id: creativeId },
    select: {
      id: true,
      title: true,
      body: true,
      callToActionType: true,
      adAccountId: true,
    },
  });
  if (!creative) {
    throw new ApiError(404, 'Not found.', 'detail');
  }

  const account = await prisma.metaAdAccount.findFirst({
    where: { id: creative.adAccountId },
    select: { projectId: true },
  });
  if (account?.projectId && account.projectId !== projectId) {
    throw new ApiError(400, 'creative_id does not belong to project_id');
  }

  return creative;
}
