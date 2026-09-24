'use client';

import { useEffect, useState } from 'react';
import { QuickReplyTemplateAPI } from '@/lib/api/csmConversationApi';
import { TemplatePicker } from '@/components/csm/conversations/ConversationComposer';
import PortalSelect from '@/components/ticket-form/portal/PortalSelect';
import type { QuickReplyTemplate, TemplatePreviewTeam } from '@/types/csmConversation';

const LABEL_CLASS = 'mb-1.5 block text-[12px] font-medium uppercase tracking-wider text-gray-500';
const NO_TEAM = 'none';

interface Props {
  organisations: { id: number; name: string }[];
  /** Organisation of the queue the conversation was routed to, if known. */
  suggestedOrganisationId: number | null;
  onInsert: (template: QuickReplyTemplate) => void;
}

/**
 * Browse templates exactly as an agent in a chosen team sees them in the
 * workspace. "Insert" only adds a local preview bubble; nothing is sent.
 */
export default function SandboxTemplateBrowser({ organisations, suggestedOrganisationId, onInsert }: Props) {
  const [organisationId, setOrganisationId] = useState<number | null>(null);
  const [teams, setTeams] = useState<TemplatePreviewTeam[]>([]);
  const [viewAsTeam, setViewAsTeam] = useState<number | 'none'>(NO_TEAM);

  const effectiveOrgId =
    organisationId
    ?? (organisations.some((o) => o.id === suggestedOrganisationId) ? suggestedOrganisationId : null)
    ?? organisations[0]?.id
    ?? null;

  useEffect(() => {
    setViewAsTeam(NO_TEAM);
    setTeams([]);
    if (effectiveOrgId === null) return;
    let cancelled = false;
    QuickReplyTemplateAPI.previewTeams(effectiveOrgId)
      .then((rows) => { if (!cancelled) setTeams(rows); })
      .catch(() => { if (!cancelled) setTeams([]); });
    return () => { cancelled = true; };
  }, [effectiveOrgId]);

  if (effectiveOrgId === null) {
    return (
      <p className="text-sm text-gray-500">
        You are not a CSM admin of any organisation, so there are no templates to preview.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="sb-tmpl-org" className={LABEL_CLASS}>Organisation</label>
          <PortalSelect
            id="sb-tmpl-org"
            value={String(effectiveOrgId)}
            options={organisations.map((o) => ({ value: String(o.id), label: o.name }))}
            onChange={(v) => setOrganisationId(Number(v))}
          />
        </div>
        <div>
          <label htmlFor="sb-tmpl-team" className={LABEL_CLASS}>View as team</label>
          <PortalSelect
            id="sb-tmpl-team"
            value={String(viewAsTeam)}
            options={[
              { value: NO_TEAM, label: 'Agent with no team' },
              ...teams.map((t) => ({ value: String(t.id), label: t.name })),
            ]}
            onChange={(v) => setViewAsTeam(v === NO_TEAM ? NO_TEAM : Number(v))}
          />
        </div>
      </div>
      <p className="text-xs text-gray-500">
        Shows workspace-wide templates plus the selected team&apos;s, as agents see them in the conversation workspace.
      </p>
      <TemplatePicker
        key={`${effectiveOrgId}-${viewAsTeam}`}
        organisationId={effectiveOrgId}
        viewAsTeam={viewAsTeam}
        renderRich
        insertLabel="Insert as preview"
        onSelect={onInsert}
      />
    </div>
  );
}
