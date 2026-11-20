'use client';

import { Loader2, Plus , Megaphone} from 'lucide-react';
import { useRouter } from 'next/navigation';
import type { CampaignData, CampaignStatus } from '@/types/campaign';
import { useProjectStore } from '@/lib/projectStore';
import { nestedProjectPathFromProject } from '@/lib/projectNestedRoutes';
import CampaignStatusPill from './pills/CampaignStatusPill';
import EmptyState from '@/components/state-feedback/EmptyState';
import { CAMPAIGN_OBJECTIVE, CAMPAIGN_PLATFORM, CAMPAIGN_STATUS } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionLabel } from '@/lib/orgCustomization/optionConfig';

interface Props {
  campaigns: CampaignData[];
  loading: boolean;
  errorMessage: string | null;
  onRowClick?: (campaign: CampaignData) => void;
  onCreate?: () => void;
}

const STATUS_DOT: Record<CampaignStatus, string> = {
  PLANNING: 'bg-gray-300',
  TESTING: 'bg-amber-400',
  SCALING: 'bg-sky-400',
  OPTIMIZING: 'bg-[#3CCED7]',
  PAUSED: 'bg-gray-400',
  COMPLETED: 'bg-[#A6E661]',
  ARCHIVED: 'bg-gray-200',
};

function formatDate(iso?: string | null) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '—';
  }
}

export default function CampaignListTable({
  campaigns,
  loading,
  errorMessage,
  onRowClick,
  onCreate,
}: Props) {
  useOptionConfigVersion();
  const router = useRouter();
  const activeProject = useProjectStore((s) => s.activeProject);

  return (
    <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
      {loading ? (
        <div className="flex items-center justify-center py-16 text-sm text-gray-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading campaigns…
        </div>
      ) : errorMessage ? (
        <div className="px-6 py-12 text-center text-sm text-rose-600">{errorMessage}</div>
      ) : campaigns.length === 0 ? (
        <EmptyState
              icon={<Megaphone className="h-5 w-5" />}
              title='No campaigns yet'
              description="Create your first campaign to start planning and tracking performance."
              actionLabel="Create campaign"
              onAction={onCreate}
              actionIcon={<Plus className="h-3.5 w-3.5" /> }
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead className="border-b border-gray-100 bg-gray-50/60 text-[11px] uppercase tracking-wider text-gray-500">
              <tr>
                <th className="w-10 px-4 py-3 text-left"></th>
                <th className="px-4 py-3 text-left">Name</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">Objective</th>
                <th className="px-4 py-3 text-left">Platforms</th>
                <th className="px-4 py-3 text-left">Owner</th>
                <th className="px-4 py-3 text-left">Start</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-gray-700">
              {campaigns.map((c) => {
                const dot = STATUS_DOT[c.status] ?? 'bg-gray-300';
                const platforms = (c.platforms || []).slice(0, 3);
                const extra = (c.platforms || []).length - platforms.length;
                return (
                  <tr
                    key={c.id}
                    className="cursor-pointer transition hover:bg-gray-50/80"
                    onClick={() => (onRowClick ? onRowClick(c) : router.push(nestedProjectPathFromProject(activeProject, `/campaigns/${c.slug}`)))}
                  >
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block h-2 w-2 rounded-full ${dot}`}
                        style={{ backgroundColor: CAMPAIGN_STATUS.colors[c.status] }}
                        title={optionLabel(CAMPAIGN_STATUS, c.status)}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{c.name || 'Untitled'}</div>
                      {c.hypothesis ? (
                        <div className="mt-0.5 line-clamp-1 text-xs text-gray-500">{c.hypothesis}</div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3">
                      <CampaignStatusPill status={c.status} />
                    </td>
                    <td className="px-4 py-3 text-xs uppercase tracking-wide text-gray-500">
                      {optionLabel(CAMPAIGN_OBJECTIVE, c.objective) || '—'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {platforms.map((p) => (
                          <span
                            key={p}
                            className="rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] text-gray-600"
                          >
                            {optionLabel(CAMPAIGN_PLATFORM, p)}
                          </span>
                        ))}
                        {extra > 0 && (
                          <span className="rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] text-gray-500">
                            +{extra}
                          </span>
                        )}
                        {platforms.length === 0 && <span className="text-xs text-gray-400">—</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600">
                      {c.owner?.username ?? '—'}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500">
                      {formatDate(c.start_date)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
