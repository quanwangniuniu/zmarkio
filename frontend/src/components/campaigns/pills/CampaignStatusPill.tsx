'use client';

import type { CampaignStatus } from '@/types/campaign';
import { CAMPAIGN_STATUS } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle } from '@/lib/orgCustomization/optionConfig';

interface Props {
  status: CampaignStatus;
  className?: string;
}

const STYLES: Record<CampaignStatus, string> = {
  PLANNING: 'bg-gray-100 text-gray-700 ring-1 ring-gray-200',
  TESTING: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  SCALING: 'bg-sky-50 text-sky-800 ring-1 ring-sky-200',
  OPTIMIZING: 'bg-[#3CCED7]/10 text-[#0E8A96] ring-1 ring-[#3CCED7]/30',
  PAUSED: 'bg-gray-200 text-gray-800 ring-1 ring-gray-300',
  COMPLETED: 'bg-[#A6E661]/20 text-[#3F6B1F] ring-1 ring-[#A6E661]/40',
  ARCHIVED: 'bg-gray-50 text-gray-500 ring-1 ring-gray-200 italic',
};

export default function CampaignStatusPill({ status, className = '' }: Props) {
  useOptionConfigVersion();
  const classes = STYLES[status];
  if (!classes) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${classes} ${className}`}
      style={optionColorStyle(CAMPAIGN_STATUS.colors[status])}
    >
      {CAMPAIGN_STATUS.labels[status]}
    </span>
  );
}
