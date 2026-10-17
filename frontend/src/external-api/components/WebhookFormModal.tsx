'use client';

import { useEffect, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import Modal from '@/components/ui/Modal';
import {
  BUILDER_CONTROL_CLASS,
  BUILDER_PRIMARY_BUTTON_CLASS,
  FORM_LABEL_CLASS,
  SECONDARY_BUTTON_CLASS,
} from '@/components/csm-settings/constants';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import type { VocabularyOption, WebhookEndpointData } from '@/external-api/types';

interface Props {
  isOpen: boolean;
  events: VocabularyOption[];
  onClose: () => void;
  /** Throws the axios error on failure so field errors can be shown. */
  onSubmit: (data: WebhookEndpointData) => Promise<void>;
}

export default function WebhookFormModal({ isOpen, events, onClose, onSubmit }: Props) {
  const [url, setUrl] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setUrl('');
    setSelected(new Set());
    setFieldErrors({});
    setServerError(null);
  }, [isOpen]);

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
    if (!url.trim()) errors.url = 'URL is required.';
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
        events: Array.from(selected),
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
    <Modal isOpen={isOpen} onClose={onClose}>
      <div className="mx-4 w-full max-w-lg rounded-xl bg-white shadow-xl">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">New webhook</h2>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col gap-5 p-6" noValidate>
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

          <div className="flex justify-end gap-2 border-t border-gray-200 pt-3">
            <button type="button" onClick={onClose} disabled={submitting} className={SECONDARY_BUTTON_CLASS}>
              Cancel
            </button>
            <button type="submit" disabled={submitting} className={BUILDER_PRIMARY_BUTTON_CLASS}>
              {submitting ? 'Saving…' : 'Create webhook'}
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
