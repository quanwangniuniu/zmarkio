'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, Copy } from 'lucide-react';
import toast from 'react-hot-toast';
import CsmAPI from '@/lib/api/csmApi';
import { ExperienceGroupAPI } from '@/lib/api/experienceGroupApi';
import { TicketFormAPI } from '@/lib/api/ticketFormApi';
import { SupportChannelAPI } from '@/lib/api/supportChannelApi';
import type { Queue } from '@/types/csm';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';
import type { TicketFormListItem } from '@/types/ticketForm';
import type {
  ChannelType,
  OfflineAlternative,
  OperatingHours,
  SupportChannelDetail,
  SupportChannelListItem,
} from '@/types/supportChannel';
import {
  CHANNEL_TYPE_LABELS,
  DEFAULT_OPERATING_HOURS,
  OFFLINE_ALTERNATIVE_LABELS,
} from '@/types/supportChannel';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import PortalSelect from '@/components/ticket-form/portal/PortalSelect';
import PortalMultiSelect from '@/components/ticket-form/portal/PortalMultiSelect';
import CsmSettingsDrawerShell from './CsmSettingsDrawerShell';
import OperatingHoursEditor, { validateOperatingHours } from './OperatingHoursEditor';
import {
  BUILDER_CONTROL_CLASS,
  DRAWER_PRIMARY_BUTTON_CLASS,
  FORM_LABEL_CLASS,
  SECONDARY_BUTTON_CLASS,
} from './constants';

const FORM_LABEL_INLINE_CLASS =
  'mb-1.5 text-[13px] font-medium uppercase tracking-wider text-gray-600';

const TIMEZONE_OPTIONS = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Asia/Tokyo',
  'Australia/Sydney',
];

interface Props {
  isOpen: boolean;
  projectId: number;
  editing: SupportChannelDetail | null;
  onClose: () => void;
  onSaved: (channel: SupportChannelDetail) => void;
}

export default function SupportChannelFormDrawer({
  isOpen,
  projectId,
  editing,
  onClose,
  onSaved,
}: Props) {
  const isEdit = editing !== null;

  const [displayName, setDisplayName] = useState('');
  const [channelType, setChannelType] = useState<ChannelType>('live_chat');
  const [isActive, setIsActive] = useState(true);
  const [welcomeMessage, setWelcomeMessage] = useState('');
  const [ticketConfirmationMessage, setTicketConfirmationMessage] = useState('');
  const [offlineMessage, setOfflineMessage] = useState('');
  const [offlineAlternative, setOfflineAlternative] = useState<OfflineAlternative>('message_only');
  const [offlineTargetId, setOfflineTargetId] = useState('');
  const [operatingHours, setOperatingHours] = useState<OperatingHours>(DEFAULT_OPERATING_HOURS);
  const [timezone, setTimezone] = useState('UTC');
  const [defaultQueueId, setDefaultQueueId] = useState('');
  const [ticketFormId, setTicketFormId] = useState('');
  const [emailAddress, setEmailAddress] = useState('');
  const [sortOrder, setSortOrder] = useState('0');

  const [queues, setQueues] = useState<Queue[]>([]);
  const [ticketForms, setTicketForms] = useState<TicketFormListItem[]>([]);
  const [contactFormChannels, setContactFormChannels] = useState<SupportChannelListItem[]>([]);
  const [experienceGroups, setExperienceGroups] = useState<ExperienceGroupListItem[]>([]);
  const [selectedEgIds, setSelectedEgIds] = useState<Set<number>>(new Set());

  const [embedSnippet, setEmbedSnippet] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [dirty, setDirty] = useState(false);

  const markDirty = useCallback(() => setDirty(true), []);

  useEffect(() => {
    if (!isOpen) return;
    setDisplayName(editing?.display_name ?? '');
    setChannelType(editing?.channel_type ?? 'live_chat');
    setIsActive(editing?.is_active ?? true);
    setWelcomeMessage(editing?.welcome_message ?? '');
    setTicketConfirmationMessage(editing?.ticket_confirmation_message ?? '');
    setOfflineMessage(editing?.offline_fallback_message ?? '');
    setOfflineAlternative(editing?.offline_alternative ?? 'message_only');
    setOfflineTargetId(
      editing?.offline_alternative_target_id != null
        ? String(editing.offline_alternative_target_id)
        : '',
    );
    setOperatingHours(editing?.operating_hours ?? DEFAULT_OPERATING_HOURS);
    setTimezone(editing?.timezone ?? 'UTC');
    setDefaultQueueId(editing?.default_queue ? String(editing.default_queue) : '');
    setTicketFormId(editing?.ticket_form ? String(editing.ticket_form) : '');
    setEmailAddress(editing?.email_address ?? '');
    setSortOrder(String(editing?.sort_order ?? 0));
    setSelectedEgIds(new Set(editing?.experience_groups.map((eg) => eg.id) ?? []));
    setEmbedSnippet('');
    setFieldErrors({});
    setServerError(null);
    setDirty(false);
  }, [isOpen, editing]);

  useEffect(() => {
    if (!isOpen || !projectId) return;
    let cancelled = false;
    (async () => {
      try {
        const [queueRows, formRows, channelRows, egRes] = await Promise.all([
          CsmAPI.listProjectQueues(projectId),
          TicketFormAPI.list(projectId),
          SupportChannelAPI.list(projectId, { includeInactive: true }),
          ExperienceGroupAPI.list({ project: projectId }),
        ]);
        if (cancelled) return;
        setQueues(queueRows);
        setTicketForms(formRows);
        setContactFormChannels(
          channelRows.filter((c) => c.channel_type === 'contact_form' && c.id !== editing?.id),
        );
        const egList = Array.isArray(egRes.data) ? egRes.data : egRes.data.results ?? [];
        setExperienceGroups(egList);
      } catch {
        if (!cancelled) {
          setQueues([]);
          setTicketForms([]);
          setContactFormChannels([]);
          setExperienceGroups([]);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [isOpen, projectId, editing?.id]);

  useEffect(() => {
    if (!isOpen || !editing || editing.channel_type !== 'live_chat') return;
    let cancelled = false;
    SupportChannelAPI.getEmbedSnippet(editing.id)
      .then((res) => { if (!cancelled) setEmbedSnippet(res.data.snippet); })
      .catch(() => { if (!cancelled) setEmbedSnippet(''); });
    return () => { cancelled = true; };
  }, [isOpen, editing]);

  const offlineTargetOptions = useMemo(() => {
    const options: { value: string; label: string }[] = [];
    contactFormChannels.forEach((ch) => {
      options.push({ value: String(ch.id), label: `Channel: ${ch.display_name}` });
    });
    ticketForms.forEach((form) => {
      options.push({ value: String(form.id), label: `Form: ${form.name}` });
    });
    return options;
  }, [contactFormChannels, ticketForms]);

  const channelTypeOptions = useMemo(
    () =>
      (Object.keys(CHANNEL_TYPE_LABELS) as ChannelType[]).map((type) => ({
        value: type,
        label: CHANNEL_TYPE_LABELS[type],
      })),
    [],
  );

  const offlineAlternativeOptions = useMemo(
    () =>
      (Object.keys(OFFLINE_ALTERNATIVE_LABELS) as OfflineAlternative[]).map((alt) => ({
        value: alt,
        label: OFFLINE_ALTERNATIVE_LABELS[alt],
      })),
    [],
  );

  const timezoneOptions = useMemo(
    () => TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz })),
    [],
  );

  const queueOptions = useMemo(
    () => queues.map((q) => ({ value: String(q.id), label: q.name })),
    [queues],
  );

  const ticketFormOptions = useMemo(
    () => ticketForms.map((form) => ({ value: String(form.id), label: form.name })),
    [ticketForms],
  );

  const experienceGroupOptions = useMemo(
    () => experienceGroups.map((eg) => ({ value: String(eg.id), label: eg.name })),
    [experienceGroups],
  );

  const selectedEgValues = useMemo(
    () => Array.from(selectedEgIds).map(String),
    [selectedEgIds],
  );

  const handleExperienceGroupsChange = (values: string[]) => {
    markDirty();
    setSelectedEgIds(new Set(values.map(Number)));
  };

  const buildPayload = () => ({
    channel_type: channelType,
    display_name: displayName.trim(),
    welcome_message: welcomeMessage,
    ticket_confirmation_message: ticketConfirmationMessage,
    offline_fallback_message: offlineMessage,
    offline_alternative: offlineAlternative,
    offline_alternative_target_id:
      offlineTargetId === '' ? null : Number(offlineTargetId),
    operating_hours: operatingHours,
    timezone,
    default_queue: defaultQueueId === '' ? null : Number(defaultQueueId),
    ticket_form: ticketFormId === '' ? null : Number(ticketFormId),
    email_address: emailAddress.trim(),
    sort_order: Number(sortOrder) || 0,
    ...(isEdit ? { is_active: isActive } : {}),
  });

  const validateClient = (): boolean => {
    const errors: Record<string, string> = {};
    if (!displayName.trim()) {
      errors.display_name = 'Display name is required.';
    }
    if (channelType === 'live_chat' && !defaultQueueId) {
      errors.default_queue = 'Live chat channels require a default queue.';
    }
    if (channelType === 'contact_form' && !ticketFormId) {
      errors.ticket_form = 'Contact form channels require a ticket form.';
    }
    if (channelType === 'email' && !emailAddress.trim()) {
      errors.email_address = 'Email address is required.';
    }
    const hoursError = validateOperatingHours(operatingHours);
    if (hoursError) {
      errors.operating_hours = hoursError;
    }
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleRequestClose = () => {
    if (dirty && !window.confirm('Discard unsaved changes?')) return;
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validateClient()) return;

    setSubmitting(true);
    setServerError(null);
    try {
      const payload = buildPayload();
      const res = isEdit
        ? await SupportChannelAPI.update(editing!.id, payload)
        : await SupportChannelAPI.create(projectId, payload as Parameters<typeof SupportChannelAPI.create>[1]);

      await SupportChannelAPI.replaceExperienceGroups(res.data.id, {
        experience_group_ids: Array.from(selectedEgIds),
      });

      const full = await SupportChannelAPI.retrieve(res.data.id);
      onSaved(full.data);
    } catch (err: unknown) {
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      const parsed = parseFieldErrors(data);
      if (Object.keys(parsed).length > 0) {
        setFieldErrors(parsed);
      } else {
        setServerError(
          isEdit ? 'Could not update support channel.' : 'Could not create support channel.',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleCopySnippet = async () => {
    if (!embedSnippet) return;
    try {
      await navigator.clipboard.writeText(embedSnippet);
      toast.success('Embed snippet copied.');
    } catch {
      toast.error('Could not copy snippet.');
    }
  };

  const sectionClass =
    'flex flex-col gap-3 -mx-6 border-b border-gray-200 px-6 pb-8 last:border-0';

  return (
    <CsmSettingsDrawerShell
      open={isOpen}
      onClose={handleRequestClose}
      title={isEdit ? 'Edit support channel' : 'New support channel'}
      footer={
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={handleRequestClose}
            disabled={submitting}
            className={SECONDARY_BUTTON_CLASS}
          >
            Cancel
          </button>
          <button
            type="submit"
            form="support-channel-form"
            disabled={submitting}
            className={DRAWER_PRIMARY_BUTTON_CLASS}
          >
            {submitting
              ? isEdit ? 'Saving...' : 'Creating...'
              : isEdit ? 'Save changes' : 'Create channel'}
          </button>
        </div>
      }
    >
      <form
        id="support-channel-form"
        onSubmit={handleSubmit}
        className="flex min-h-0 flex-1 flex-col overflow-hidden"
      >
        <div className="flex flex-1 flex-col overflow-y-auto px-6 py-6 [&>section:not(:first-of-type)]:pt-8">
          {serverError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {serverError}
            </div>
          )}

          <section className={sectionClass}>
            <h3 className={FORM_LABEL_CLASS}>General</h3>
            <div>
              <label htmlFor="sc-name" className={FORM_LABEL_INLINE_CLASS}>
                Display name <span className="text-red-500">*</span>
              </label>
              <input
                id="sc-name"
                type="text"
                value={displayName}
                onChange={(e) => { markDirty(); setDisplayName(e.target.value); }}
                disabled={submitting}
                className={BUILDER_CONTROL_CLASS}
              />
              {fieldErrors.display_name && (
                <p className="text-xs text-red-600">{fieldErrors.display_name}</p>
              )}
            </div>
            <div>
              <label htmlFor="sc-type" className={FORM_LABEL_INLINE_CLASS}>
                Channel type
              </label>
              <PortalSelect
                id="sc-type"
                value={channelType}
                options={channelTypeOptions}
                disabled={submitting || isEdit}
                onChange={(v) => { markDirty(); setChannelType(v as ChannelType); }}
              />
            </div>
            {isEdit && (
              <label className={`flex items-center gap-2 ${FORM_LABEL_INLINE_CLASS}`}>
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => { markDirty(); setIsActive(e.target.checked); }}
                  disabled={submitting}
                />
                Active
              </label>
            )}
          </section>

          <section className={sectionClass}>
            <h3 className={FORM_LABEL_CLASS}>Messages</h3>
            <div>
              <label htmlFor="sc-welcome" className={FORM_LABEL_INLINE_CLASS}>
                Welcome message
              </label>
              <textarea
                id="sc-welcome"
                rows={3}
                value={welcomeMessage}
                onChange={(e) => { markDirty(); setWelcomeMessage(e.target.value); }}
                disabled={submitting}
                className={BUILDER_CONTROL_CLASS}
              />
            </div>
            <div>
              <label htmlFor="sc-ticket-confirm" className={FORM_LABEL_INLINE_CLASS}>
                Ticket confirmation message
              </label>
              <textarea
                id="sc-ticket-confirm"
                rows={3}
                value={ticketConfirmationMessage}
                onChange={(e) => { markDirty(); setTicketConfirmationMessage(e.target.value); }}
                disabled={submitting}
                placeholder="Sent to the customer when a ticket is created from their conversation. Leave blank to send nothing."
                className={BUILDER_CONTROL_CLASS}
              />
            </div>
            <div>
              <label htmlFor="sc-offline" className={FORM_LABEL_INLINE_CLASS}>
                Offline fallback message
              </label>
              <textarea
                id="sc-offline"
                rows={2}
                value={offlineMessage}
                onChange={(e) => { markDirty(); setOfflineMessage(e.target.value); }}
                disabled={submitting}
                className={BUILDER_CONTROL_CLASS}
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="sc-alt" className={FORM_LABEL_INLINE_CLASS}>
                  Offline alternative
                </label>
                <PortalSelect
                  id="sc-alt"
                  value={offlineAlternative}
                  options={offlineAlternativeOptions}
                  disabled={submitting}
                  onChange={(v) => { markDirty(); setOfflineAlternative(v as OfflineAlternative); }}
                />
              </div>
              {offlineAlternative === 'contact_form' && (
                <div>
                  <label htmlFor="sc-alt-target" className={FORM_LABEL_INLINE_CLASS}>
                    Alternative target
                  </label>
                  <PortalSelect
                    id="sc-alt-target"
                    value={offlineTargetId}
                    options={offlineTargetOptions}
                    placeholder="Select…"
                    disabled={submitting}
                    onChange={(v) => { markDirty(); setOfflineTargetId(v); }}
                  />
                </div>
              )}
            </div>
          </section>

          <section className={sectionClass}>
            <h3 className={FORM_LABEL_CLASS}>Operating hours</h3>
            <div>
              <label htmlFor="sc-tz" className={FORM_LABEL_INLINE_CLASS}>
                Timezone
              </label>
              <PortalSelect
                id="sc-tz"
                value={timezone}
                options={timezoneOptions}
                disabled={submitting}
                onChange={(v) => { markDirty(); setTimezone(v); }}
              />
            </div>
            <OperatingHoursEditor
              value={operatingHours}
              onChange={(hours) => { markDirty(); setOperatingHours(hours); }}
              disabled={submitting}
            />
            {fieldErrors.operating_hours && (
              <p className="text-xs text-red-600">{fieldErrors.operating_hours}</p>
            )}
          </section>

          <section className={sectionClass}>
            <h3 className={FORM_LABEL_CLASS}>Routing</h3>
            {channelType === 'live_chat' && (
              <div>
                <label htmlFor="sc-queue" className={FORM_LABEL_INLINE_CLASS}>
                  Default queue <span className="text-red-500">*</span>
                </label>
                <PortalSelect
                  id="sc-queue"
                  value={defaultQueueId}
                  options={queueOptions}
                  placeholder="Select queue…"
                  disabled={submitting}
                  onChange={(v) => { markDirty(); setDefaultQueueId(v); }}
                />
                {fieldErrors.default_queue && (
                  <p className="text-xs text-red-600">{fieldErrors.default_queue}</p>
                )}
              </div>
            )}
            {channelType === 'contact_form' && (
              <div>
                <label htmlFor="sc-form" className={FORM_LABEL_INLINE_CLASS}>
                  Ticket form <span className="text-red-500">*</span>
                </label>
                <PortalSelect
                  id="sc-form"
                  value={ticketFormId}
                  options={ticketFormOptions}
                  placeholder="Select form…"
                  disabled={submitting}
                  onChange={(v) => { markDirty(); setTicketFormId(v); }}
                />
                {fieldErrors.ticket_form && (
                  <p className="text-xs text-red-600">{fieldErrors.ticket_form}</p>
                )}
              </div>
            )}
            {channelType === 'email' && (
              <div>
                <label htmlFor="sc-email" className={FORM_LABEL_CLASS}>
                  Email address <span className="text-red-500">*</span>
                </label>
                <input
                  id="sc-email"
                  type="email"
                  value={emailAddress}
                  onChange={(e) => { markDirty(); setEmailAddress(e.target.value); }}
                  disabled={submitting}
                  className={BUILDER_CONTROL_CLASS}
                />
                {fieldErrors.email_address && (
                  <p className="text-xs text-red-600">{fieldErrors.email_address}</p>
                )}
              </div>
            )}
            <div>
              <label htmlFor="sc-sort" className={FORM_LABEL_INLINE_CLASS}>
                Sort order
              </label>
              <input
                id="sc-sort"
                type="number"
                min={0}
                value={sortOrder}
                onChange={(e) => { markDirty(); setSortOrder(e.target.value); }}
                disabled={submitting}
                className={BUILDER_CONTROL_CLASS}
              />
            </div>
          </section>

          <section className={sectionClass}>
            <h3 className={FORM_LABEL_CLASS}>Experience groups</h3>
            {experienceGroups.length === 0 ? (
              <p className="text-sm text-gray-500">No experience groups in this project.</p>
            ) : (
              <PortalMultiSelect
                id="sc-experience-groups"
                values={selectedEgValues}
                options={experienceGroupOptions}
                placeholder="Select experience groups…"
                disabled={submitting}
                onChange={handleExperienceGroupsChange}
              />
            )}
          </section>

          {isEdit && channelType === 'live_chat' && embedSnippet && (
            <section className={sectionClass}>
              <div className="flex items-center justify-between">
                <h3 className={FORM_LABEL_CLASS}>Embed snippet</h3>
                <button
                  type="button"
                  onClick={handleCopySnippet}
                  className={SECONDARY_BUTTON_CLASS}
                >
                  <Copy className="h-4 w-4" aria-hidden />
                  Copy
                </button>
              </div>
              <textarea
                readOnly
                rows={4}
                value={embedSnippet}
                className={`${BUILDER_CONTROL_CLASS} font-mono text-xs`}
              />
            </section>
          )}
        </div>
      </form>
    </CsmSettingsDrawerShell>
  );
}
