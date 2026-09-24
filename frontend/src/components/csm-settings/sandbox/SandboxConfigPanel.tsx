'use client';

import { useMemo } from 'react';
import { RotateCcw } from 'lucide-react';
import PortalSelect from '@/components/ticket-form/portal/PortalSelect';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';
import { CHANNEL_TYPE_LABELS, type SupportChannelListItem } from '@/types/supportChannel';
import { BUILDER_CONTROL_CLASS, SECONDARY_BUTTON_CLASS } from '../constants';
import type { SandboxConfig } from './useRoutingSandbox';

const LABEL_CLASS = 'mb-1.5 block text-[12px] font-medium uppercase tracking-wider text-gray-500';
const NONE = '';

interface Props {
  config: SandboxConfig;
  experienceGroups: ExperienceGroupListItem[];
  channels: SupportChannelListItem[];
  organisations: { id: number; name: string }[];
  onChange: (config: SandboxConfig) => void;
  onReset: () => void;
}

export default function SandboxConfigPanel({
  config,
  experienceGroups,
  channels,
  organisations,
  onChange,
  onReset,
}: Props) {
  const update = (patch: Partial<SandboxConfig>) => onChange({ ...config, ...patch });

  // Channels assigned to the selected group first; the rest are still testable.
  const channelOptions = useMemo(() => {
    const linked = (c: SupportChannelListItem) =>
      c.experience_groups.some((g) => g.id === config.experienceGroupId);
    const sorted = [...channels].sort((a, b) => Number(linked(b)) - Number(linked(a)));
    return [
      { value: NONE, label: 'No channel (organisation fallback)' },
      ...sorted.map((c) => ({
        value: String(c.id),
        label: `${c.display_name} · ${CHANNEL_TYPE_LABELS[c.channel_type]}${
          linked(c) ? '' : ' (not in this group)'
        }${c.is_active ? '' : ' (inactive)'}`,
      })),
    ];
  }, [channels, config.experienceGroupId]);

  const toId = (value: string) => (value === NONE ? null : Number(value));

  return (
    <div className="flex flex-col gap-4" aria-label="Sandbox scenario">
      <div>
        <label htmlFor="sb-group" className={LABEL_CLASS}>
          Experience group <span className="text-red-500">*</span>
        </label>
        <PortalSelect
          id="sb-group"
          value={config.experienceGroupId === null ? NONE : String(config.experienceGroupId)}
          options={experienceGroups.map((g) => ({ value: String(g.id), label: g.name }))}
          placeholder="Select a group…"
          onChange={(v) => update({ experienceGroupId: toId(v) })}
        />
      </div>

      <div>
        <label htmlFor="sb-channel" className={LABEL_CLASS}>Channel</label>
        <PortalSelect
          id="sb-channel"
          value={config.supportChannelId === null ? NONE : String(config.supportChannelId)}
          options={channelOptions}
          onChange={(v) => update({ supportChannelId: toId(v) })}
        />
      </div>

      <div>
        <label htmlFor="sb-org" className={LABEL_CLASS}>Customer organisation</label>
        <PortalSelect
          id="sb-org"
          value={config.customerOrganisationId === null ? NONE : String(config.customerOrganisationId)}
          options={[
            { value: NONE, label: 'None' },
            ...organisations.map((o) => ({ value: String(o.id), label: o.name })),
          ]}
          onChange={(v) => update({ customerOrganisationId: toId(v) })}
        />
      </div>

      <div>
        <label htmlFor="sb-subject" className={LABEL_CLASS}>Subject</label>
        <input
          id="sb-subject"
          value={config.subject}
          maxLength={200}
          onChange={(e) => update({ subject: e.target.value })}
          placeholder="Optional"
          className={BUILDER_CONTROL_CLASS}
        />
      </div>

      <div>
        <label htmlFor="sb-time" className={LABEL_CLASS}>Simulated time</label>
        <input
          id="sb-time"
          type="datetime-local"
          value={config.simulatedAt}
          onChange={(e) => update({ simulatedAt: e.target.value })}
          className={BUILDER_CONTROL_CLASS}
        />
        <p className="mt-1 text-xs text-gray-500">
          Leave empty for now. Used for the channel&apos;s operating hours.
        </p>
      </div>

      <button type="button" onClick={onReset} className={`self-start ${SECONDARY_BUTTON_CLASS}`}>
        <RotateCcw className="h-4 w-4" aria-hidden />
        Reset conversation
      </button>
    </div>
  );
}
