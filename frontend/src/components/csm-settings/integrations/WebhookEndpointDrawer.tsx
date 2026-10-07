'use client';

import { useEffect, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import CsmSettingsDrawerShell from '@/components/csm-settings/CsmSettingsDrawerShell';
import {
  BUILDER_CONTROL_CLASS,
  DRAWER_PRIMARY_BUTTON_CLASS,
  FORM_LABEL_CLASS,
  SECONDARY_BUTTON_CLASS,
} from '@/components/csm-settings/constants';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import type { VocabularyOption, WebhookEndpoint, WebhookEndpointData } from '@/types/csmIntegrations';

/** Client-side check only; the server also rejects hosts that resolve to private addresses. */
export function validateWebhookUrl(url: string): string | null {
  const trimmed = url.trim();
  if (!trimmed) return 'URL is required.';
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'https:') return 'Use an https:// URL.';
  } catch {
    return 'Enter a valid URL.';
  }
  return null;
}

interface Props {
  isOpen: boolean;
  editing: WebhookEndpoint | null;
  events: VocabularyOption[];
  onClose: () => void;
  /** Throws the axios error on failure so field errors can be shown. */
  onSubmit: (data: WebhookEndpointData) => Promise<void>;
}

export default function WebhookEndpointDrawer({ isOpen, editing, events, onClose, onSubmit }: Props) {
  const isEdit = editing !== null;
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [isActive, setIsActive] = useState(true);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setUrl(editing?.url ?? '');
    setDescription(editing?.description ?? '');
    setSelected(new Set(editing?.events ?? []));
    setIsActive(editing?.is_active ?? true);
    setFieldErrors({});
    setServerError(null);
  }, [isOpen, editing]);

  const toggleEvent = (event: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(event);
      else next.delete(event);
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errors: Record<string, string> = {};
    const urlError = validateWebhookUrl(url);
    if (urlError) errors.url = urlError;
    if (selected.size === 0) errors.events = 'Subscribe to at least one event.';
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      return;
    }
    setSubmitting(true);
    setFieldErrors({});
    setServerError(null);
    try {
      await onSubmit({
        url: url.trim(),
        description: description.trim(),
        events: events.map(({ value }) => value).filter((event) => selected.has(event)),
        is_active: isActive,
      });
    } catch (err: unknown) {
      const parsed = parseFieldErrors((err as { response?: { data?: unknown } })?.response?.data);
      if (Object.keys(parsed).length) setFieldErrors(parsed);
      else setServerError('Could not save the webhook.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <CsmSettingsDrawerShell
      open={isOpen}
      onClose={onClose}
      title={isEdit ? 'Edit webhook' : 'New webhook'}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={submitting} className={SECONDARY_BUTTON_CLASS}>
            Cancel
          </button>
          <button type="submit" form="webhook-endpoint-form" disabled={submitting} className={DRAWER_PRIMARY_BUTTON_CLASS}>
            {submitting ? 'Saving…' : isEdit ? 'Save webhook' : 'Create webhook'}
          </button>
        </div>
      }
    >
      <form
        id="webhook-endpoint-form"
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

          <div>
            <label htmlFor="webhook-url" className={FORM_LABEL_CLASS}>
              Endpoint URL <span className="text-red-500">*</span>
            </label>
            <input
              id="webhook-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/webhooks/zmarkio"
              maxLength={2000}
              disabled={submitting}
              className={`w-full ${BUILDER_CONTROL_CLASS}`}
            />
            {fieldErrors.url && <p className="mt-1 text-xs text-red-600">{fieldErrors.url}</p>}
          </div>

          <div>
            <label htmlFor="webhook-description" className={FORM_LABEL_CLASS}>Description</label>
            <input
              id="webhook-description"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. Escalations to PagerDuty"
              maxLength={200}
              disabled={submitting}
              className={`w-full ${BUILDER_CONTROL_CLASS}`}
            />
          </div>

          <fieldset>
            <legend className={FORM_LABEL_CLASS}>
              Events <span className="text-red-500">*</span>
            </legend>
            <div className="flex flex-col gap-2">
              {events.map(({ value, label }) => (
                <label key={value} className="flex items-center gap-2 text-sm text-gray-800">
                  <input
                    type="checkbox"
                    checked={selected.has(value)}
                    disabled={submitting}
                    onChange={(e) => toggleEvent(value, e.target.checked)}
                  />
                  {label}
                  <span className="font-mono text-xs text-gray-500">{value}</span>
                </label>
              ))}
            </div>
            {fieldErrors.events && <p className="mt-1 text-xs text-red-600">{fieldErrors.events}</p>}
          </fieldset>

          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={isActive}
              disabled={submitting}
              onChange={(e) => setIsActive(e.target.checked)}
            />
            <span className="font-medium">Active</span>
            <span className="text-gray-500">— inactive webhooks receive nothing, and pending retries stop.</span>
          </label>
        </div>
      </form>
    </CsmSettingsDrawerShell>
  );
}
