'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { AlertCircle, ListTree, MessageSquareText, Settings2 } from 'lucide-react';
import CsmSettingsPageRoot, { CsmSettingsProjectGuard } from '@/components/csm-settings/CsmSettingsPageRoot';
import { useProjectIdFromUrl } from '@/components/csm-settings/useProjectIdFromUrl';
import { SECONDARY_BUTTON_CLASS } from '@/components/csm-settings/constants';
import { useRoutingOptions } from '@/components/csm-settings/routing/useRoutingOptions';
import RoutingTracePanel from '@/components/csm-settings/sandbox/RoutingTracePanel';
import SandboxChatSimulator from '@/components/csm-settings/sandbox/SandboxChatSimulator';
import SandboxConfigPanel from '@/components/csm-settings/sandbox/SandboxConfigPanel';
import SandboxTemplateBrowser from '@/components/csm-settings/sandbox/SandboxTemplateBrowser';
import { useRoutingSandbox } from '@/components/csm-settings/sandbox/useRoutingSandbox';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { useBuildUrl } from '@/lib/buildUrl';

type SideTab = 'trace' | 'templates';

export default function RoutingSandboxPage() {
  const { projectId, projectValid } = useProjectIdFromUrl();
  const buildUrl = useBuildUrl();
  const options = useRoutingOptions(projectId, projectValid);
  const sandbox = useRoutingSandbox(projectId);
  const [tab, setTab] = useState<SideTab>('trace');
  const { config, setConfig } = sandbox;

  // Default to the first experience group once options load.
  useEffect(() => {
    if (config.experienceGroupId === null && options.experienceGroups.length > 0) {
      setConfig({ ...config, experienceGroupId: options.experienceGroups[0].id });
    }
  }, [config, options.experienceGroups, setConfig]);

  const latestQueueId = sandbox.traces[sandbox.traces.length - 1]?.outcome.queue_id ?? null;
  const suggestedOrganisationId =
    options.queues.find((q) => q.id === latestQueueId)?.organisation ?? null;

  const tabClass = (active: boolean) =>
    `inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ${
      active ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-800'
    }`;

  return (
    <CsmSettingsPageRoot>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Routing &amp; Template Sandbox</h1>
          <p className="mt-1 text-sm text-gray-500">
            Simulate a customer conversation to see how routing rules evaluate and preview templates as agents see
            them. Nothing is saved, sent, or counted in live queues. Live conversations are routed once, on the first
            message; later traces show how the rules read the conversation so far.
          </p>
        </div>
        {projectValid && (
          <Link href={buildUrl('/admin/csm/settings/routing-rules')} className={SECONDARY_BUTTON_CLASS}>
            <Settings2 className="h-4 w-4" aria-hidden />
            Edit routing rules
          </Link>
        )}
      </div>

      {!projectValid ? (
        <CsmSettingsProjectGuard />
      ) : options.loading ? (
        <div className="flex min-h-[300px] items-center justify-center"><LoadingSpinner /></div>
      ) : options.error ? (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
          {options.error}
        </div>
      ) : options.experienceGroups.length === 0 ? (
        <p className="text-sm text-gray-500">Create an experience group first to run the sandbox.</p>
      ) : (
        <>
          <section className="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
            <SandboxConfigPanel
              config={config}
              experienceGroups={options.experienceGroups}
              channels={options.channels}
              organisations={options.organisations}
              onChange={setConfig}
              onReset={sandbox.reset}
            />
          </section>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">

            <SandboxChatSimulator
              messages={sandbox.messages}
              disabled={config.experienceGroupId === null || sandbox.customerTexts.length >= 50}
              disabledReason={
                config.experienceGroupId === null
                  ? 'Select an experience group first'
                  : 'Message limit reached; reset the conversation'
              }
              onSend={sandbox.sendCustomerMessage}
            />

            <div className="flex min-w-0 flex-col gap-3">
              <div className="flex gap-1 rounded-lg bg-gray-100 p-1" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === 'trace'}
                  onClick={() => setTab('trace')}
                  className={tabClass(tab === 'trace')}
                >
                  <ListTree className="h-4 w-4" aria-hidden />
                  Routing trace
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === 'templates'}
                  onClick={() => setTab('templates')}
                  className={tabClass(tab === 'templates')}
                >
                  <MessageSquareText className="h-4 w-4" aria-hidden />
                  Templates
                </button>
              </div>

              {tab === 'trace' ? (
                <RoutingTracePanel
                  traces={sandbox.traces}
                  customerMessages={sandbox.customerTexts}
                  meta={sandbox.meta}
                  evaluating={sandbox.evaluating}
                  error={sandbox.error}
                  lookups={options.lookups}
                  onRerun={sandbox.rerun}
                />
              ) : (
                <SandboxTemplateBrowser
                  organisations={options.organisations}
                  suggestedOrganisationId={suggestedOrganisationId}
                  onInsert={sandbox.insertTemplate}
                />
              )}
            </div>
          </div>
        </>
      )}
    </CsmSettingsPageRoot>
  );
}
