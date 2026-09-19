'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import Modal from '@/components/ui/Modal';
import PortalMultiSelect from '@/components/ticket-form/portal/PortalMultiSelect';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import CsmGuidanceAPI from '@/lib/api/csmGuidanceApi';
import {
  GUIDANCE_TYPE_OPTIONS,
  type GuidanceEntry,
  type GuidanceType,
} from '@/types/csmGuidance';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';
import { BUILDER_CONTROL_CLASS, BUILDER_PRIMARY_BUTTON_CLASS, SECONDARY_BUTTON_CLASS } from './constants';

interface Props {
  isOpen: boolean;
  projectId: number;
  editing: GuidanceEntry | null;
  experienceGroups: ExperienceGroupListItem[];
  /** Pre-selected group for a new entry (the group currently being viewed). */
  defaultExperienceGroupId: number | null;
  onClose: () => void;
  onSaved: (entry: GuidanceEntry) => void;
}

export default function GuidanceFormModal({
  isOpen,
  projectId,
  editing,
  experienceGroups,
  defaultExperienceGroupId,
  onClose,
  onSaved,
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
        ? await CsmGuidanceAPI.update(editing!.id, payload)
        : await CsmGuidanceAPI.create(projectId, payload);
      onSaved(saved);
    } catch (err: unknown) {
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
      <div className="mx-4 w-full max-w-xl rounded-xl bg-white shadow-xl">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {isEdit ? 'Edit guidance' : 'New guidance'}
          </h2>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-6" noValidate>
          {serverError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {serverError}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label htmlFor="guidance-type" className="text-sm font-medium text-gray-700">
              Type <span className="text-red-500">*</span>
            </label>
            <select
              id="guidance-type"
              value={guidanceType}
              onChange={(e) => setGuidanceType(e.target.value as GuidanceType)}
              disabled={submitting}
              className={BUILDER_CONTROL_CLASS}
            >
              {GUIDANCE_TYPE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            {fieldErrors.guidance_type && (
              <p className="text-xs text-red-600">{fieldErrors.guidance_type}</p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="guidance-trigger" className="text-sm font-medium text-gray-700">
              Trigger description <span className="text-red-500">*</span>
            </label>
            <textarea
              id="guidance-trigger"
              rows={3}
              value={trigger}
              onChange={(e) => setTrigger(e.target.value)}
              placeholder="e.g. The customer asks for a refund more than 30 days after purchase."
              disabled={submitting}
              maxLength={2000}
              className={BUILDER_CONTROL_CLASS}
            />
            {fieldErrors.trigger_description && (
              <p className="text-xs text-red-600">{fieldErrors.trigger_description}</p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="guidance-response" className="text-sm font-medium text-gray-700">
              Recommended response or action <span className="text-red-500">*</span>
            </label>
            <textarea
              id="guidance-response"
              rows={6}
              value={response}
              onChange={(e) => setResponse(e.target.value)}
              placeholder="What the agent should say or do."
              disabled={submitting}
              maxLength={10000}
              className={BUILDER_CONTROL_CLASS}
            />
            {fieldErrors.recommended_response && (
              <p className="text-xs text-red-600">{fieldErrors.recommended_response}</p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            {/* PortalMultiSelect's trigger is a div combobox, which <label> cannot target. */}
            <p className="text-sm font-medium text-gray-700">
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
            {fieldErrors.experience_group_ids && (
              <p className="text-xs text-red-600">{fieldErrors.experience_group_ids}</p>
            )}
          </div>

          <div className="flex justify-end gap-2 border-t border-gray-200 pt-2">
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
