'use client';

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertCircle, Copy, Plus, Trash2 } from 'lucide-react';
import CsmSettingsPageRoot, { CsmSettingsProjectGuard } from '@/components/csm-settings/CsmSettingsPageRoot';
import CsmSettingsDrawerShell from '@/components/csm-settings/CsmSettingsDrawerShell';
import SettingsHubLink from '@/components/csm-settings/SettingsHubLink';
import { useProjectIdFromUrl } from '@/components/csm-settings/useProjectIdFromUrl';
import {
  BUILDER_CONTROL_CLASS,
  DRAWER_PRIMARY_BUTTON_CLASS,
  FORM_LABEL_CLASS,
  SECONDARY_BUTTON_CLASS,
} from '@/components/csm-settings/constants';
import { parseFieldErrors } from '@/components/ticket-form/formErrors';
import { PORTAL_SUBMIT_BUTTON_CLASS } from '@/components/ticket-form/constants';
import ConfirmModal from '@/components/ui/ConfirmModal';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import api from '@/lib/api';

const BASE = '/api/external-api';
const CHECKBOX_CLASS = 'h-4 w-4 cursor-pointer rounded border-gray-300 text-[#3CCED7] focus:ring-[#3CCED7]';

interface Overview {
  resources: string[];
  events: { value: string; label: string }[];
  credentials: { id: number; name: string; kind: 'api_key' | 'client'; public_id: string; scopes: string[] }[];
  webhooks: { id: number; url: string; events: string[] }[];
  deliveries: { id: number; url: string; event_type: string; attempt: number; response_code: number | null; created_at: string }[];
}

type DrawerKind = 'credential' | 'webhook';

// What the confirm modal is about to remove: revoke a credential or delete a webhook.
interface Removal { path: string; title: string; message: string; done: string; failed: string }

function when(value: string) {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export default function IntegrationsSettingsPage() {
  const { projectId, projectValid } = useProjectIdFromUrl();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [drawer, setDrawer] = useState<DrawerKind | null>(null);
  const [removing, setRemoving] = useState<Removal | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!projectValid) return;
    setLoadError(null);
    setForbidden(false);
    try {
      const res = await api.get<Overview>(`${BASE}/`, { params: { project: projectId } });
      setOverview(res.data);
    } catch (err) {
      if ((err as { response?: { status?: number } })?.response?.status === 403) setForbidden(true);
      else setLoadError('Could not load API access.');
    }
  }, [projectId, projectValid]);

  useEffect(() => { load(); }, [load]);

  const eventLabel = (value: string) => overview?.events.find((item) => item.value === value)?.label ?? value;

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await api.delete(`${BASE}/${removing.path}/`, { params: { project: projectId } });
      setRemoving(null);
      toast.success(removing.done);
      load();
    } catch {
      toast.error(removing.failed);
    } finally {
      setBusy(false);
    }
  };

  return (
    <CsmSettingsPageRoot>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">API &amp; Webhooks</h1>
          <p className="mt-1 text-sm text-gray-500">
            Keys for external systems, and signed callbacks when a ticket changes or an SLA is breached.
          </p>
        </div>
        {projectValid && (
          <div className="flex flex-wrap items-center gap-3">
            <SettingsHubLink projectId={projectId} />
            {!forbidden && (
              <>
                <button type="button" onClick={() => setDrawer('webhook')} disabled={!overview} className={SECONDARY_BUTTON_CLASS}>
                  <Plus className="h-4 w-4" aria-hidden />
                  New webhook
                </button>
                <button
                  type="button"
                  onClick={() => setDrawer('credential')}
                  disabled={!overview}
                  className={`gap-2 ${PORTAL_SUBMIT_BUTTON_CLASS}`}
                >
                  <Plus className="h-4 w-4" aria-hidden />
                  New credential
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {!projectValid ? (
        <CsmSettingsProjectGuard />
      ) : forbidden ? (
        <p className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
          Only an organisation admin can manage API access and webhooks.
        </p>
      ) : loadError ? (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
          {loadError}
          <button type="button" onClick={load} className="ml-auto rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-100">
            Retry
          </button>
        </div>
      ) : !overview ? (
        <div className="flex min-h-[300px] items-center justify-center">
          <LoadingSpinner />
        </div>
      ) : (
        <div className="flex flex-col gap-8">
          <Section title="Credentials" hint="API keys go in X-API-Key. Bearer clients exchange their secret for a one-hour token." empty={overview.credentials.length === 0}>
            {overview.credentials.map((row) => (
              <div key={row.id} className="flex items-center gap-3 bg-white px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900">{row.name}</p>
                  <p className="mt-1 text-xs text-gray-600">
                    {row.kind === 'api_key' ? 'API key' : 'Bearer client'}
                    <span className="mx-1.5 text-gray-300">·</span>
                    <span className="font-mono">{row.public_id}</span>
                    <span className="mx-1.5 text-gray-300">·</span>
                    {row.scopes.length ? row.scopes.join(', ') : 'No access'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setRemoving({
                    path: `credentials/${row.id}`,
                    title: 'Revoke credential?',
                    message: `Requests using “${row.name}” will be rejected straight away.`,
                    done: 'Credential revoked.',
                    failed: 'Could not revoke the credential.',
                  })}
                  className="rounded-lg p-2 text-gray-400 hover:bg-red-50 hover:text-red-600"
                  aria-label={`Revoke ${row.name}`}
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                </button>
              </div>
            ))}
          </Section>

          <Section title="Webhooks" hint="HTTPS endpoints that receive a signed POST for the events they subscribe to." empty={overview.webhooks.length === 0}>
            {overview.webhooks.map((hook) => (
              <div key={hook.id} className="flex items-center gap-3 bg-white px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="break-all text-sm font-medium text-gray-900">{hook.url}</p>
                  <p className="mt-1 text-xs text-gray-600">{hook.events.map(eventLabel).join(', ')}</p>
                </div>
                <button
                  type="button"
                  onClick={() => setRemoving({
                    path: `webhooks/${hook.id}`,
                    title: 'Delete webhook?',
                    message: `Events will stop going to ${hook.url}.`,
                    done: 'Webhook deleted.',
                    failed: 'Could not delete the webhook.',
                  })}
                  className="rounded-lg p-2 text-gray-400 hover:bg-red-50 hover:text-red-600"
                  aria-label={`Delete webhook ${hook.url}`}
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                </button>
              </div>
            ))}
          </Section>

          <Section title="Delivery log" hint="Every attempt, newest first." empty={overview.deliveries.length === 0}>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    {['Event', 'Response', 'Attempt', 'Endpoint', 'Time'].map((heading) => (
                      <th key={heading} className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {overview.deliveries.map((row) => (
                    <tr key={row.id} className="bg-white">
                      <td className="whitespace-nowrap px-4 py-3 font-medium text-gray-900">{eventLabel(row.event_type)}</td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-700">{row.response_code ?? 'No response'}</td>
                      <td className="px-4 py-3 text-gray-700">{row.attempt}</td>
                      <td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-gray-600" title={row.url}>{row.url}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-600">{when(row.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </div>
      )}

      {overview && (
        <CreateDrawer
          kind={drawer}
          projectId={projectId}
          resources={overview.resources}
          events={overview.events}
          onClose={() => setDrawer(null)}
          onCreated={load}
        />
      )}

      <ConfirmModal
        isOpen={removing !== null}
        onClose={() => { if (!busy) setRemoving(null); }}
        onConfirm={remove}
        loading={busy}
        title={removing?.title ?? ''}
        message={removing?.message ?? ''}
        confirmText="Confirm"
      />
    </CsmSettingsPageRoot>
  );
}

function Section({ title, hint, empty, children }: { title: string; hint: string; empty: boolean; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h2>
        <p className="mt-1 text-xs text-gray-400">{hint}</p>
      </div>
      {empty ? (
        <p className="text-sm text-gray-500">Nothing yet.</p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 divide-y divide-gray-100">{children}</div>
      )}
    </section>
  );
}

function CreateDrawer({
  kind,
  projectId,
  resources,
  events: available,
  onClose,
  onCreated,
}: {
  kind: DrawerKind | null;
  projectId: number;
  resources: string[];
  events: Overview['events'];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState('');
  const [credentialKind, setCredentialKind] = useState<'api_key' | 'client'>('api_key');
  const [scopes, setScopes] = useState<string[]>([]);
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);

  useEffect(() => {
    if (!kind) return;
    setConfirmClose(false);
    setName('');
    setCredentialKind('api_key');
    setScopes([]);
    setUrl('');
    setEvents([]);
    setError(null);
    setFieldErrors({});
    setSecret(null);
  }, [kind]);

  const isCredential = kind === 'credential';

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const missing: Record<string, string> = {};
    if (isCredential && !name.trim()) missing.name = 'Name is required.';
    if (!isCredential && !url.trim()) missing.url = 'HTTPS URL is required.';
    if (!isCredential && events.length === 0) missing.events = 'Choose at least one event.';
    setFieldErrors(missing);
    setError(null);
    if (Object.keys(missing).length > 0) return;
    setSubmitting(true);
    try {
      const params = { params: { project: projectId } };
      const res = isCredential
        ? await api.post(`${BASE}/credentials/`, { name: name.trim(), kind: credentialKind, scopes }, params)
        : await api.post(`${BASE}/webhooks/`, { url: url.trim(), events }, params);
      setSecret(res.data.secret);
      toast.success(isCredential ? 'Credential created.' : 'Webhook created.');
      onCreated();
    } catch (err) {
      const data = (err as { response?: { data?: { detail?: unknown } } })?.response?.data;
      const parsed = parseFieldErrors(data);
      if (Object.keys(parsed).length > 0) setFieldErrors(parsed);
      else if (typeof data?.detail === 'string') setError(data.detail);
      else setError(isCredential ? 'Could not create the credential.' : 'Could not create the webhook.');
    } finally {
      setSubmitting(false);
    }
  };

  // The secret is shown once, so a stray click outside or Escape must not lose it.
  const requestClose = () => (secret ? setConfirmClose(true) : onClose());

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret ?? '');
      toast.success('Secret copied.');
    } catch {
      toast.error('Could not copy. Select the text and copy it manually.');
    }
  };

  return (
    <>
      <CsmSettingsDrawerShell
        open={kind !== null}
        onClose={requestClose}
        title={secret ? 'Copy your secret' : isCredential ? 'New credential' : 'New webhook'}
        footer={secret ? (
          <div className="flex justify-end">
            <button type="button" onClick={onClose} className={DRAWER_PRIMARY_BUTTON_CLASS}>Done</button>
          </div>
        ) : (
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} disabled={submitting} className={SECONDARY_BUTTON_CLASS}>Cancel</button>
            <button type="submit" form="create-form" disabled={submitting} className={DRAWER_PRIMARY_BUTTON_CLASS}>
              {submitting ? 'Creating…' : 'Create'}
            </button>
          </div>
        )}
      >
        {secret ? (
          <div className="flex flex-col gap-3 px-6 pb-6">
            <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              Copy this secret now. It will not be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-sm text-gray-900">
                {secret}
              </code>
              <button type="button" onClick={copy} className={SECONDARY_BUTTON_CLASS}>
                <Copy className="h-4 w-4" aria-hidden />
                Copy
              </button>
            </div>
          </div>
        ) : (
          <form id="create-form" onSubmit={submit} className="flex flex-col gap-5 px-6 pb-6" noValidate>
            {error && (
              <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
                {error}
              </div>
            )}
            {isCredential ? (
              <>
                <div>
                  <label htmlFor="credential-name" className={FORM_LABEL_CLASS}>
                    Name <span className="text-red-500">*</span>
                  </label>
                  <input id="credential-name" value={name} onChange={(e) => { setName(e.target.value); setFieldErrors({}); }} maxLength={200} className={BUILDER_CONTROL_CLASS} autoFocus />
                  <FieldError message={fieldErrors.name} />
                </div>
                <div>
                  <label htmlFor="credential-kind" className={FORM_LABEL_CLASS}>Kind</label>
                  <select
                    id="credential-kind"
                    value={credentialKind}
                    onChange={(e) => setCredentialKind(e.target.value as 'api_key' | 'client')}
                    className={BUILDER_CONTROL_CLASS}
                  >
                    <option value="api_key">API key</option>
                    <option value="client">Bearer client</option>
                  </select>
                </div>
                <fieldset className="flex flex-col gap-2">
                  <legend className={FORM_LABEL_CLASS}>Access</legend>
                  <p className="text-xs text-gray-500">Read allows GET. Write also allows create, update and delete.</p>
                  <div className="overflow-hidden rounded-xl border border-gray-200 divide-y divide-gray-100">
                    <div className="grid grid-cols-[minmax(0,1fr)_5rem_5rem] bg-gray-50 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                      <span>Resource</span>
                      <span className="text-center">Read</span>
                      <span className="text-center">Write</span>
                    </div>
                    {resources.map((resource) => {
                      const label = resource.replace('_', ' ');
                      return (
                        <div key={resource} className="grid grid-cols-[minmax(0,1fr)_5rem_5rem] items-center bg-white px-4 py-2.5 text-sm text-gray-800">
                          <span className="capitalize">{label}</span>
                          {['read', 'write'].map((access) => {
                            const scope = `${resource}:${access}`;
                            return (
                              <span key={access} className="flex justify-center">
                                <input
                                  type="checkbox"
                                  className={CHECKBOX_CLASS}
                                  aria-label={`${label} ${access}`}
                                  checked={scopes.includes(scope)}
                                  onChange={() => setScopes((current) => (
                                    current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope]
                                  ))}
                                />
                              </span>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>
                </fieldset>
              </>
            ) : (
              <>
                <div>
                  <label htmlFor="webhook-url" className={FORM_LABEL_CLASS}>
                    HTTPS URL <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="webhook-url"
                    value={url}
                    onChange={(e) => { setUrl(e.target.value); setFieldErrors({}); }}
                    placeholder="https://example.com/hooks"
                    className={BUILDER_CONTROL_CLASS}
                    autoFocus
                  />
                  <FieldError message={fieldErrors.url} />
                </div>
                <fieldset className="flex flex-col gap-2">
                  <legend className={FORM_LABEL_CLASS}>
                    Events <span className="text-red-500">*</span>
                  </legend>
                  {available.map((item) => (
                    <label key={item.value} className="flex items-center gap-3 text-sm text-gray-800">
                      <input
                        type="checkbox"
                        className={CHECKBOX_CLASS}
                        checked={events.includes(item.value)}
                        onChange={() => {
                          setEvents((current) => (
                            current.includes(item.value) ? current.filter((value) => value !== item.value) : [...current, item.value]
                          ));
                          setFieldErrors({});
                        }}
                      />
                      {item.label}
                    </label>
                  ))}
                  <FieldError message={fieldErrors.events} />
                </fieldset>
              </>
            )}
          </form>
        )}
      </CsmSettingsDrawerShell>
      <ConfirmModal
        isOpen={confirmClose}
        onClose={() => setConfirmClose(false)}
        onConfirm={onClose}
        title="Close without copying?"
        message="This secret will not be shown again."
        confirmText="Close"
        type="warning"
      />
    </>
  );
}

function FieldError({ message }: { message?: string }) {
  return message ? <p className="mt-1 text-xs text-red-600">{message}</p> : null;
}
