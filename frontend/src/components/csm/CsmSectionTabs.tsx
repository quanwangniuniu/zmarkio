'use client';

import Link from 'next/link';
import { useBuildUrl } from '@/lib/buildUrl';

/** Tabs rendered in place on /csm; linked to as `/csm?tab=<id>` from elsewhere. */
export const CSM_SECTION_TABS = [
  { id: 'organisations', label: 'Organisations' },
  { id: 'queues', label: 'Queues' },
  { id: 'regions', label: 'Regions' },
  { id: 'users', label: 'CSM Users' },
] as const;

export type CsmSectionTabId = (typeof CSM_SECTION_TABS)[number]['id'];

const BASE_CLASS =
  'px-4 py-2.5 text-sm font-medium border-b-2 transition-colors';
const ACTIVE_CLASS = 'border-[#3CCED7] text-[#1a9ba3]';
const INACTIVE_CLASS =
  'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300';

interface Props {
  /** The selected in-page tab, when rendered on /csm. */
  activeTab?: CsmSectionTabId;
  /** Provided by /csm so the section tabs switch in place rather than navigate. */
  onSelectTab?: (id: CsmSectionTabId) => void;
  /** Highlights one of the standalone pages instead of an in-page tab. */
  activeSection?: 'conversations' | 'templates' | 'settings';
  /** The Settings link is admin-only. */
  showSettings?: boolean;
}

/**
 * The Customer Service section nav, shared by /csm and the settings pages so
 * every screen in the section can reach the others.
 */
export default function CsmSectionTabs({
  activeTab,
  onSelectTab,
  activeSection,
  showSettings = false,
}: Props) {
  const buildUrl = useBuildUrl();

  return (
    <nav className="flex items-center gap-1 border-b border-gray-200">
      {CSM_SECTION_TABS.map((tab) =>
        onSelectTab ? (
          <button
            key={tab.id}
            onClick={() => onSelectTab(tab.id)}
            className={`${BASE_CLASS} ${activeTab === tab.id ? ACTIVE_CLASS : INACTIVE_CLASS}`}
          >
            {tab.label}
          </button>
        ) : (
          <Link
            key={tab.id}
            href={buildUrl(`/csm?tab=${tab.id}`)}
            className={`${BASE_CLASS} ${INACTIVE_CLASS}`}
          >
            {tab.label}
          </Link>
        )
      )}
      <Link
        href={buildUrl('/csm/conversations')}
        className={`ml-2 ${BASE_CLASS} ${activeSection === 'conversations' ? ACTIVE_CLASS : INACTIVE_CLASS}`}
      >
        Conversations
      </Link>
      <Link
        href={buildUrl('/csm/templates')}
        className={`${BASE_CLASS} ${activeSection === 'templates' ? ACTIVE_CLASS : INACTIVE_CLASS}`}
      >
        Templates
      </Link>
      {showSettings && (
        <Link
          href={buildUrl('/admin/csm/settings')}
          className={`${BASE_CLASS} ${activeSection === 'settings' ? ACTIVE_CLASS : INACTIVE_CLASS}`}
        >
          Settings
        </Link>
      )}
    </nav>
  );
}
