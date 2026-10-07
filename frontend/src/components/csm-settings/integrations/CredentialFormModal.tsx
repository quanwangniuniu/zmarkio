'use client';

import { useEffect, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import Modal from '@/components/ui/Modal';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import {
  BUILDER_CONTROL_CLASS,
  BUILDER_PRIMARY_BUTTON_CLASS,
  SECONDARY_BUTTON_CLASS,
} from '@/components/csm-settings/constants';
import type { CreateCredentialData } from '@/types/csmIntegrations';
import { resourceLabel } from './labels';

interface Props {
  isOpen: boolean;
  title: string;
  resources: string[];
  onClose: () => void;
  /** Throws the axios error on failure so field errors can be shown. */
  onSubmit: (data: CreateCredentialData) => Promise<void>;
}

/** Name + a read/write scope grid. Write implies read, so ticking write ticks (and locks) read. */
export default function CredentialFormModal({ isOpen, title, resources, onClose, onSubmit }: Props) {
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<Set<string>>(new Set());
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setName('');
    setScopes(new Set());
    setFieldErrors({});
    setServerError(null);
  }, [isOpen]);

  const toggle = (resource: string, access: 'read' | 'write', checked: boolean) => {
    setScopes((prev) => {
      const next = new Set(prev);
      const scope = `${resource}:${access}`;
      if (checked) {
        next.add(scope);
        if (access === 'write') next.add(`${resource}:read`);
      } else {
        next.delete(scope);
      }
      return next;
    });
  };

  const setAll = (access: 'read' | 'write' | 'none') => {
    if (access === 'none') {
      setScopes(new Set());
      return;
    }
    const next = new Set<string>();
    resources.forEach((resource) => {
      next.add(`${resource}:read`);
      if (access === 'write') next.add(`${resource}:write`);
    });
    setScopes(next);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = 'Name is required.';
    if (scopes.size === 0) errors.scopes = 'Choose at least one permission.';
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      return;
    }
    setSubmitting(true);
    setFieldErrors({});
    setServerError(null);
    try {
      await onSubmit({ name: name.trim(), scopes: Array.from(scopes) });
    } catch (err: unknown) {
      const parsed = parseFieldErrors((err as { response?: { data?: unknown } })?.response?.data);
      if (Object.keys(parsed).length) setFieldErrors(parsed);
      else setServerError('Could not create the credential.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose}>
      <div className="mx-4 w-full max-w-lg rounded-xl bg-white shadow-xl">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-6">
          {serverError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {serverError}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label htmlFor="credential-name" className="text-sm font-medium text-gray-700">
              Name <span className="text-red-500">*</span>
            </label>
            <input
              id="credential-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Salesforce sync"
              maxLength={200}
              disabled={submitting}
              className={BUILDER_CONTROL_CLASS}
            />
            {fieldErrors.name && <p className="text-xs text-red-600">{fieldErrors.name}</p>}
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 flex w-full items-center justify-between text-sm font-medium text-gray-700">
              <span>Permissions <span className="text-red-500">*</span></span>
              <span className="flex gap-3 text-xs font-normal">
                <button type="button" className="text-indigo-600 hover:underline" onClick={() => setAll('read')}>
                  All read
                </button>
                <button type="button" className="text-indigo-600 hover:underline" onClick={() => setAll('write')}>
                  All read &amp; write
                </button>
                <button type="button" className="text-gray-500 hover:underline" onClick={() => setAll('none')}>
                  Clear
                </button>
              </span>
            </legend>
            <div className="overflow-hidden rounded-lg border border-gray-200">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                  <tr>
                    <th className="px-3 py-2">Resource</th>
                    <th className="px-3 py-2 text-center">Read</th>
                    <th className="px-3 py-2 text-center">Write</th>
                  </tr>
                </thead>
                <tbody>
                  {resources.map((resource) => {
                    const canWrite = scopes.has(`${resource}:write`);
                    return (
                      <tr key={resource} className="border-t border-gray-100">
                        <td className="px-3 py-2 text-gray-800">{resourceLabel(resource)}</td>
                        <td className="px-3 py-2 text-center">
                          <input
                            type="checkbox"
                            aria-label={`${resourceLabel(resource)} read`}
                            checked={scopes.has(`${resource}:read`)}
                            disabled={submitting || canWrite}
                            onChange={(e) => toggle(resource, 'read', e.target.checked)}
                          />
                        </td>
                        <td className="px-3 py-2 text-center">
                          <input
                            type="checkbox"
                            aria-label={`${resourceLabel(resource)} write`}
                            checked={canWrite}
                            disabled={submitting}
                            onChange={(e) => toggle(resource, 'write', e.target.checked)}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {fieldErrors.scopes && <p className="text-xs text-red-600">{fieldErrors.scopes}</p>}
          </fieldset>

          <div className="flex justify-end gap-2 border-t border-gray-200 pt-2">
            <button type="button" onClick={onClose} disabled={submitting} className={SECONDARY_BUTTON_CLASS}>
              Cancel
            </button>
            <button type="submit" disabled={submitting} className={BUILDER_PRIMARY_BUTTON_CLASS}>
              {submitting ? 'Creating…' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
