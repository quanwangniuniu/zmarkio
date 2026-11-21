'use client';

import { useEffect, useState } from 'react';
import { Search, X } from 'lucide-react';
import { CAMPAIGN_STATUS } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionChoices } from '@/lib/orgCustomization/optionConfig';

interface Props {
  searchQuery: string;
  statusFilter: string;
  onSearchChange: (q: string) => void;
  onStatusChange: (status: string) => void;
}

export default function CampaignListFilters({
  searchQuery,
  statusFilter,
  onSearchChange,
  onStatusChange,
}: Props) {
  useOptionConfigVersion();
  const [local, setLocal] = useState(searchQuery);

  useEffect(() => {
    const t = setTimeout(() => onSearchChange(local), 300);
    return () => clearTimeout(t);
  }, [local, onSearchChange]);

  return (
    <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center">
      <div className="relative w-full sm:flex-1">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          placeholder="Search campaigns by name or hypothesis"
          className="w-full truncate rounded-md border border-gray-200 bg-white py-2 pl-9 pr-2 text-sm outline-none transition placeholder:text-gray-400 focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
        />
        {local && (
          <button
            type="button"
            onClick={() => setLocal('')}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="Clear search"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <select
        value={statusFilter}
        onChange={(e) => onStatusChange(e.target.value)}
        className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20 sm:w-44"
      >
        {[{ value: 'all', label: 'All statuses' }, ...optionChoices(CAMPAIGN_STATUS)].map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}
