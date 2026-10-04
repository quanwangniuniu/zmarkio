'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, ArrowRightLeft, MessageSquareQuote, Siren, StickyNote } from 'lucide-react';
import Modal from '@/components/ui/Modal';
import PortalMultiSelect from '@/components/ticket-form/portal/PortalMultiSelect';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import { GuidanceEntryCard } from '@/components/csm/conversations/GuidanceEntryCard';
import CsmGuidanceAPI, { isGuidanceConflict } from '@/lib/api/csmGuidanceApi';
import {
  GUIDANCE_TYPE_OPTIONS,
  type GuidanceEntry,
  type GuidanceType,
} from '@/types/csmGuidance';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';
import { BUILDER_CONTROL_CLASS, BUILDER_PRIMARY_BUTTON_CLASS, SECONDARY_BUTTON_CLASS } from './constants';

const TYPE_ICONS: Record<GuidanceType, typeof ArrowRightLeft> = {
  handoff: ArrowRightLeft,
  suggested_reply: MessageSquareQuote,
  escalation_procedure: Siren,
  process_note: StickyNote,
};

const TYPE_HINTS: Record<GuidanceType, string> = {
  handoff: 'Pass the conversation on',
  suggested_reply: 'Wording to send',
  escalation_procedure: 'Steps to escalate',
  process_note: 'Context to keep in mind',
};

const SECTION_LABEL_CLASS = 'text-xs font-semibold uppercase tracking-wide text-gray-500';

interface Props {
  isOpen: boolean;
  projectId: number;
  editing: GuidanceEntry | null;
  experienceGroups: ExperienceGroupListItem[];
  /** Pre-selected group for a new entry (the group currently being viewed). */
  defaultExperienceGroupId: number | null;
  onClose: () => void;
  onSaved: (entry: GuidanceEntry) => void;
  /** Called when the save was rejected because the entry changed underneath. */
  onConflict?: () => void;
}

export default function GuidanceFormModal({
  isOpen,
  projectId,
  editing,
  experienceGroups,
  defaultExperienceGroupId,
  onClose,
  onSaved,
  onConflict,
}: Props) {
  const isEdit = editing !== null;
  const [guidanceType, setGuidanceType] = useState<GuidanceType>('suggested_reply');
  const [trigger, setTrigger] = useState('');
  const [response, setResponse] = useState('');
  const [groupValues, setGroupValues] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setGuidanceType(editing?.guidance_type ?? 'suggested_reply');
    setTrigger(editing?.trigger_description ?? '');
    setResponse(editing?.recommended_response ?? '');
    setGroupValues(
      editing
        ? editing.experience_groups.map((g) => String(g.id))
        : defaultExperienceGroupId != null ? [String(defaultExperienceGroupId)] : [],
    );
    setFieldErrors({});
    setServerError(null);
  }, [isOpen, editing, defaultExperienceGroupId]);

  const groupOptions = useMemo(
    () => experienceGroups.map((eg) => ({ value: String(eg.id), label: eg.name })),
    [experienceGroups],
  );

  const typeLabel = GUIDANCE_TYPE_OPTIONS.find((o) => o.value === guidanceType)?.label ?? '';

  const validate = () => {
    const errors: Record<string, string> = {};
    if (!trigger.trim()) errors.trigger_description = 'Describe when this guidance applies.';
    if (!response.trim()) errors.recommended_response = 'Enter the recommended response or action.';
    if (groupValues.length === 0) errors.experience_group_ids = 'Select at least one experience group.';
    return errors;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errors = validate();
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setSubmitting(true);
    setFieldErrors({});
    setServerError(null);
    const payload = {
      guidance_type: guidanceType,
      trigger_description: trigger.trim(),
      recommended_response: response.trim(),
      experience_group_ids: groupValues.map(Number),
    };
    try {
      const saved = isEdit
        ? await CsmGuidanceAPI.update(editing!.id, payload, editing!.updated_at)
        : await CsmGuidanceAPI.create(projectId, payload);
      onSaved(saved);
    } catch (err: unknown) {
      if (isGuidanceConflict(err)) {
        setServerError(
          'Someone else changed this entry while you were editing. Close and reopen it to see their changes.',
        );
        onConflict?.();
        return;
      }
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      const parsed = parseFieldErrors(data);
      if (Object.keys(parsed).length > 0) {
        setFieldErrors(parsed);
      } else {
        setServerError(isEdit ? 'Could not update guidance.' : 'Could not create guidance.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose}>
      {/* Modal centres a shrink-to-fit wrapper, so width must be explicit here. */}
      <div className="flex max-h-[90vh] w-[56rem] max-w-[calc(100vw-2rem)] flex-col rounded-xl bg-white shadow-xl">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {isEdit ? 'Edit guidance' : 'New guidance'}
          </h2>
          <p className="mt-0.5 text-sm text-gray-500">
            Shown to agents in the conversation workspace when a matching conversation is open.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <div className="grid min-h-0 flex-1 gap-6 overflow-y-auto p-6 md:grid-cols-[minmax(0,19rem)_minmax(0,1fr)]">
            {/* Left: what kind of guidance this is, and who sees it */}
            <div className="flex flex-col gap-5">
              {serverError && (
                <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
                  {serverError}
                </div>
              )}

              <fieldset className="flex flex-col gap-1.5">
                <legend className={SECTION_LABEL_CLASS}>
                  Type <span className="text-red-500">*</span>
                </legend>
                <div className="mt-1 grid grid-cols-2 gap-2">
                  {GUIDANCE_TYPE_OPTIONS.map((opt) => {
                    const Icon = TYPE_ICONS[opt.value];
                    const selected = guidanceType === opt.value;
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        aria-label={opt.label}
                        onClick={() => setGuidanceType(opt.value)}
                        disabled={submitting}
                        className={`flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors disabled:opacity-50 ${
                          selected
                            ? 'border-[#3CCED7] bg-[#3CCED7]/10 ring-1 ring-[#3CCED7]'
                            : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        <Icon
                          className={`h-4 w-4 ${selected ? 'text-[#1a9ba3]' : 'text-gray-400'}`}
                          aria-hidden
                        />
                        <span className="text-sm font-medium leading-tight text-gray-900">
                          {opt.label}
                        </span>
                        <span className="text-[11px] leading-tight text-gray-500">
                          {TYPE_HINTS[opt.value]}
                        </span>
                      </button>
                    );
                  })}
                </div>
                {fieldErrors.guidance_type && (
                  <p className="text-xs text-red-600">{fieldErrors.guidance_type}</p>
                )}
              </fieldset>

              <div className="flex flex-col gap-1.5">
                {/* PortalMultiSelect's trigger is a div combobox, which <label> cannot target. */}
                <p className={SECTION_LABEL_CLASS}>
                  Experience groups <span className="text-red-500">*</span>
                </p>
                {groupOptions.length === 0 ? (
                  <p className="text-sm text-gray-500">
                    No experience groups in this project. Create one first.
                  </p>
                ) : (
                  <PortalMultiSelect
                    id="guidance-groups"
                    values={groupValues}
                    options={groupOptions}
                    placeholder="Select experience groups…"
                    disabled={submitting}
                    onChange={setGroupValues}
                  />
                )}
                <p className="text-[11px] text-gray-500">
                  Agents see this entry on conversations matched to these groups.
                </p>
                {fieldErrors.experience_group_ids && (
                  <p className="text-xs text-red-600">{fieldErrors.experience_group_ids}</p>
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <p className={SECTION_LABEL_CLASS}>Agent preview</p>
                {/* The real workspace card, so the preview cannot drift from it. */}
                <ul className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <GuidanceEntryCard
                    entry={{
                      id: editing?.id ?? 0,
                      guidance_type: guidanceType,
                      guidance_type_display: typeLabel,
                      trigger_description: trigger.trim() || 'Trigger description',
                      recommended_response: response.trim() || 'Recommended response',
                      display_order: 0,
                    }}
                    expanded
                    canInsert={false}
                    previewOnly
                    onToggle={() => {}}
                    onInsert={() => {}}
                  />
                </ul>
              </div>
            </div>

            {/* Right: the text itself */}
            <div className="flex min-h-0 flex-col gap-5">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="guidance-trigger" className={SECTION_LABEL_CLASS}>
                  Trigger description <span className="text-red-500">*</span>
                </label>
                <textarea
                  id="guidance-trigger"
                  rows={4}
                  value={trigger}
                  onChange={(e) => setTrigger(e.target.value)}
                  placeholder="e.g. The customer asks for a refund more than 30 days after purchase."
                  disabled={submitting}
                  maxLength={2000}
                  className={`${BUILDER_CONTROL_CLASS} resize-y`}
                />
                <p className="text-[11px] text-gray-500">
                  Plain language — this is what an agent scans to spot a match.
                </p>
                {fieldErrors.trigger_description && (
                  <p className="text-xs text-red-600">{fieldErrors.trigger_description}</p>
                )}
              </div>

              <div className="flex min-h-0 flex-1 flex-col gap-1.5">
                <label htmlFor="guidance-response" className={SECTION_LABEL_CLASS}>
                  Recommended response or action <span className="text-red-500">*</span>
                </label>
                <textarea
                  id="guidance-response"
                  rows={12}
                  value={response}
                  onChange={(e) => setResponse(e.target.value)}
                  placeholder="What the agent should say or do."
                  disabled={submitting}
                  maxLength={10000}
                  className={`${BUILDER_CONTROL_CLASS} min-h-[14rem] flex-1 resize-y`}
                />
                <p className="text-[11px] text-gray-500">
                  Line breaks are kept when an agent inserts this into a reply.
                </p>
                {fieldErrors.recommended_response && (
                  <p className="text-xs text-red-600">{fieldErrors.recommended_response}</p>
                )}
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-2 border-t border-gray-200 px-6 py-4">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className={SECONDARY_BUTTON_CLASS}
            >
              Cancel
            </button>
            <button type="submit" disabled={submitting} className={BUILDER_PRIMARY_BUTTON_CLASS}>
              {submitting
                ? isEdit ? 'Saving...' : 'Creating...'
                : isEdit ? 'Save changes' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
