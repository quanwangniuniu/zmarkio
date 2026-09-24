'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Plus } from 'lucide-react';
import { RoutingRuleAPI } from '@/lib/api/routingRuleApi';
import type { Queue } from '@/types/csm';
import type {
  RoutingCondition,
  RoutingMatchMode,
  RoutingRule,
  RoutingRulePayload,
  RoutingVocabulary,
} from '@/types/routingRule';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import PortalSelect from '@/components/ticket-form/portal/PortalSelect';
import CsmSettingsDrawerShell from '../CsmSettingsDrawerShell';
import {
  BUILDER_CONTROL_CLASS,
  DRAWER_PRIMARY_BUTTON_CLASS,
  SECONDARY_BUTTON_CLASS,
} from '../constants';
import ConditionRowEditor, { newCondition } from './ConditionRowEditor';
import KeywordChipsInput from './KeywordChipsInput';

const LABEL_CLASS = 'mb-1.5 block text-[12px] font-medium uppercase tracking-wider text-gray-500';
const SECTION_CLASS = 'flex flex-col gap-3 -mx-6 border-b border-gray-200 px-6 pb-6 last:border-0';

const MATCH_MODE_OPTIONS = [
  { value: 'all', label: 'All conditions must match' },
  { value: 'any', label: 'Any condition can match' },
];

interface Props {
  isOpen: boolean;
  projectId: number;
  experienceGroupId: number;
  editing: RoutingRule | null;
  vocabulary: RoutingVocabulary;
  queues: Queue[];
  channels: { id: number; display_name: string }[];
  organisations: { id: number; name: string }[];
  onClose: () => void;
  onSaved: (rule: RoutingRule) => void;
}

export default function RoutingRuleFormDrawer({
  isOpen,
  projectId,
  experienceGroupId,
  editing,
  vocabulary,
  queues,
  channels,
  organisations,
  onClose,
  onSaved,
}: Props) {
  const isEdit = editing !== null;
  const [name, setName] = useState('');
  const [isEnabled, setIsEnabled] = useState(true);
  const [matchMode, setMatchMode] = useState<RoutingMatchMode>('all');
  const [conditions, setConditions] = useState<RoutingCondition[]>([]);
  const [targetQueueId, setTargetQueueId] = useState('');
  const [addTags, setAddTags] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [conditionErrors, setConditionErrors] = useState<string[]>([]);
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setName(editing?.name ?? '');
    setIsEnabled(editing?.is_enabled ?? true);
    setMatchMode(editing?.match_mode ?? 'all');
    setConditions(editing?.conditions ?? [newCondition(vocabulary)]);
    setTargetQueueId(editing?.target_queue ? String(editing.target_queue) : '');
    setAddTags(editing?.add_tags ?? []);
    setFieldErrors({});
    setConditionErrors([]);
    setServerError(null);
  }, [isOpen, editing, vocabulary]);

  const queueOptions = useMemo(
    () => queues.filter((q) => q.is_active).map((q) => ({ value: String(q.id), label: q.name })),
    [queues],
  );

  const updateCondition = (index: number, next: RoutingCondition) =>
    setConditions((prev) => prev.map((c, i) => (i === index ? next : c)));

  const validateClient = () => {
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = 'Name is required.';
    if (!targetQueueId) errors.target_queue = 'Choose a queue to route to.';
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validateClient()) return;
    setSubmitting(true);
    setServerError(null);
    setConditionErrors([]);
    const payload: RoutingRulePayload = {
      experience_group: experienceGroupId,
      name: name.trim(),
      is_enabled: isEnabled,
      match_mode: matchMode,
      conditions,
      target_queue: Number(targetQueueId),
      add_tags: addTags,
    };
    try {
      const saved = isEdit
        ? await RoutingRuleAPI.update(editing!.id, payload)
        : await RoutingRuleAPI.create(projectId, payload);
      onSaved(saved);
    } catch (err: unknown) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const rawConditions = data?.conditions;
      if (Array.isArray(rawConditions)) setConditionErrors(rawConditions.map(String));
      const parsed = parseFieldErrors(data);
      delete parsed.conditions;
      setFieldErrors(parsed);
      if (!Array.isArray(rawConditions) && Object.keys(parsed).length === 0) {
        setServerError(isEdit ? 'Could not update rule.' : 'Could not create rule.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <CsmSettingsDrawerShell
      open={isOpen}
      onClose={onClose}
      title={isEdit ? 'Edit routing rule' : 'New routing rule'}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={submitting} className={SECONDARY_BUTTON_CLASS}>
            Cancel
          </button>
          <button
            type="submit"
            form="routing-rule-form"
            disabled={submitting}
            className={DRAWER_PRIMARY_BUTTON_CLASS}
          >
            {submitting ? 'Saving…' : isEdit ? 'Save rule' : 'Create rule'}
          </button>
        </div>
      }
    >
      <form
        id="routing-rule-form"
        onSubmit={handleSubmit}
        className="flex min-h-0 flex-1 flex-col overflow-y-auto"
        noValidate
      >
        <div className="flex flex-col gap-6 px-6 pb-6">
          {serverError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {serverError}
            </div>
          )}

          <section className={SECTION_CLASS}>
            <div>
              <label htmlFor="rr-name" className={LABEL_CLASS}>
                Rule name <span className="text-red-500">*</span>
              </label>
              <input
                id="rr-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={submitting}
                maxLength={200}
                className={BUILDER_CONTROL_CLASS}
              />
              {fieldErrors.name && <p className="mt-1 text-xs text-red-600">{fieldErrors.name}</p>}
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={isEnabled}
                onChange={(e) => setIsEnabled(e.target.checked)}
                disabled={submitting}
              />
              Enabled
            </label>
          </section>

          <section className={SECTION_CLASS}>
            <h3 className={LABEL_CLASS}>Conditions</h3>
            <PortalSelect
              id="rr-match-mode"
              value={matchMode}
              options={MATCH_MODE_OPTIONS}
              disabled={submitting}
              onChange={(v) => setMatchMode(v as RoutingMatchMode)}
            />
            {conditions.length === 0 && (
              <p className="text-sm text-gray-500">No conditions: this rule matches every conversation.</p>
            )}
            {conditions.map((condition, index) => (
              <ConditionRowEditor
                // Rows have no stable id; index keys are fine because rows are only appended/removed.
                key={index}
                index={index}
                condition={condition}
                vocabulary={vocabulary}
                channels={channels}
                organisations={organisations}
                disabled={submitting}
                onChange={(next) => updateCondition(index, next)}
                onRemove={() => setConditions((prev) => prev.filter((_, i) => i !== index))}
              />
            ))}
            {conditionErrors.length > 0 && (
              <ul className="list-disc pl-5 text-xs text-red-600" data-testid="condition-errors">
                {conditionErrors.map((msg) => <li key={msg}>{msg}</li>)}
              </ul>
            )}
            <button
              type="button"
              onClick={() => setConditions((prev) => [...prev, newCondition(vocabulary)])}
              disabled={submitting || conditions.length >= vocabulary.limits.conditions}
              className={`self-start ${SECONDARY_BUTTON_CLASS}`}
            >
              <Plus className="h-4 w-4" aria-hidden />
              Add condition
            </button>
          </section>

          <section className={SECTION_CLASS}>
            <h3 className={LABEL_CLASS}>Action</h3>
            <div>
              <label htmlFor="rr-queue" className={LABEL_CLASS}>
                Route to queue <span className="text-red-500">*</span>
              </label>
              <PortalSelect
                id="rr-queue"
                value={targetQueueId}
                options={queueOptions}
                placeholder="Select queue…"
                disabled={submitting}
                onChange={setTargetQueueId}
              />
              {fieldErrors.target_queue && (
                <p className="mt-1 text-xs text-red-600">{fieldErrors.target_queue}</p>
              )}
            </div>
            <div>
              <label htmlFor="rr-tags" className={LABEL_CLASS}>Add tags (optional)</label>
              <KeywordChipsInput
                id="rr-tags"
                values={addTags}
                onChange={setAddTags}
                max={vocabulary.limits.tags}
                placeholder="Type a tag and press Enter"
                disabled={submitting}
              />
              {fieldErrors.add_tags && (
                <p className="mt-1 text-xs text-red-600">{fieldErrors.add_tags}</p>
              )}
            </div>
          </section>
        </div>
      </form>
    </CsmSettingsDrawerShell>
  );
}
