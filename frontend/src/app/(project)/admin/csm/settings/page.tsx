'use client';

import Link from 'next/link';
import { FileText, FolderKanban, Lightbulb, ListOrdered, Radio, Tag } from 'lucide-react';
import CsmSettingsPageRoot, { CsmSettingsProjectGuard } from '@/components/csm-settings/CsmSettingsPageRoot';
import CsmSettingsNavCard from '@/components/csm-settings/CsmSettingsNavCard';
import { useProjectIdFromUrl } from '@/components/csm-settings/useProjectIdFromUrl';
import { SECONDARY_BUTTON_CLASS } from '@/components/csm-settings/constants';
import { useBuildUrl } from '@/lib/buildUrl';

export default function CsmSettingsHubPage() {
  const { projectValid } = useProjectIdFromUrl();
  const buildUrl = useBuildUrl();

  return (
    <CsmSettingsPageRoot>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Customer Service Settings</h1>
          <p className="mt-1 text-sm text-gray-500">
            Configure support projects, work types, and support channels for request forms.
          </p>
        </div>
        {projectValid && (
          <Link
            href={buildUrl('/admin/ticket-forms')}
            className={SECONDARY_BUTTON_CLASS}
          >
            <FileText className="h-4 w-4" aria-hidden />
            Ticket Forms
          </Link>
        )}
      </div>

      {!projectValid ? (
        <CsmSettingsProjectGuard />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <CsmSettingsNavCard
            href={buildUrl('/admin/csm/settings/support-projects')}
            icon={FolderKanban}
            title="Support Projects"
            description="Classify tickets by support area. Optional default queue per project."
          />
          <CsmSettingsNavCard
            href={buildUrl('/admin/csm/settings/channels')}
            icon={Radio}
            title="Support Channels"
            description="Live chat, contact forms, and email. Operating hours and EG assignments."
          />
          <CsmSettingsNavCard
            href={buildUrl('/admin/csm/settings/work-types')}
            icon={ListOrdered}
            title="Work Types"
            description="Define request kinds shown on ticket forms. Drag to reorder."
          />
          <CsmSettingsNavCard
            href={buildUrl('/admin/csm/settings/customer-status-labels')}
            icon={Tag}
            title="Customer Status Labels"
            description="Create, color, and reorder labels used to segment customers."
          />
          <CsmSettingsNavCard
            href={buildUrl('/admin/csm/settings/guidance')}
            icon={Lightbulb}
            title="Agent Guidance"
            description="Handoffs, suggested replies, and procedures shown to agents per Experience Group."
          />
        </div>
      )}
    </CsmSettingsPageRoot>
  );
}
