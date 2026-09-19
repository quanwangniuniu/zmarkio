'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { X, Bot, Settings } from 'lucide-react';
import { AgentLayoutProvider } from './AgentLayoutContext';
import { AgentChatPage } from './chat/AgentChatPage';
import { useAgentSidePanelStore } from '@/lib/agentSidePanelStore';
import { GenerationOutputsSettings } from './chat/GenerationOutputsSettings';
import { AgentAPI } from '@/lib/api/agentApi';
import { AgentMessageBoardRail } from './AgentMessageBoardRail';
import { AgentRegistryStatusBanner } from './AgentRegistryStatusBanner';
import { AgentPanelToggleIcon } from './AgentPanelToggleIcon';
import { useProjectStore } from '@/lib/projectStore';
import TokenBadge from '@/components/plans/TokenBadge';

const MIN_WIDTH = 340;
const MAX_WIDTH = 560;

export default function AgentSidePanel() {
  const router = useRouter();
  const { isOpen, close } = useAgentSidePanelStore();
  const activeProjectId = useProjectStore((s) => s.activeProject?.id ?? null);
  const [width, setWidth] = useState(MAX_WIDTH);
  const [messageBoardsOpen, setMessageBoardsOpen] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [approvalRequired, setApprovalRequired] = useState(false);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(MAX_WIDTH);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true;
    startX.current = e.clientX;
    startWidth.current = width;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  }, [width]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = startX.current - e.clientX;
      const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta));
      setWidth(newWidth);
    };
    const onMouseUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  // Initialize approval state when opening the panel (covers first open + refresh).
  useEffect(() => {
    if (!isOpen) return;
    const prefRaw = localStorage.getItem('agent-approval-required-default');
    if (prefRaw === 'true' || prefRaw === 'false') {
      setApprovalRequired(prefRaw === 'true');
    }
    const stored = sessionStorage.getItem('agent-session-id');
    if (!stored) {
      setSessionId(null);
      return;
    }
    setSessionId(stored);
    AgentAPI.getSession(stored)
      .then((s) => setApprovalRequired(Boolean(s.approval_required)))
      .catch(() => {
        // keep last known value
      });
  }, [isOpen]);

  useEffect(() => {
    if (isOpen) return;
    setMessageBoardsOpen(true);
  }, [isOpen]);

  // Sync from AgentChatPage broadcasts.
  useEffect(() => {
    const onSessionId = (e: Event) => {
      const detail = (e as CustomEvent).detail as { sessionId?: string | null } | undefined;
      if (!detail) return;
      setSessionId(detail.sessionId ?? null);
    };
    const onSessionState = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { sessionId?: string; approvalRequired?: boolean }
        | undefined;
      if (!detail?.sessionId) return;
      setSessionId(String(detail.sessionId));
      if (typeof detail.approvalRequired === 'boolean') {
        setApprovalRequired(detail.approvalRequired);
      }
    };
    window.addEventListener('agent:session-id', onSessionId);
    window.addEventListener('agent:session-state', onSessionState);
    return () => {
      window.removeEventListener('agent:session-id', onSessionId);
      window.removeEventListener('agent:session-state', onSessionState);
    };
  }, []);

  return (
    <>
      {isOpen && (
        <button
          type="button"
          aria-label="Close AI Agent overlay"
          className="fixed bottom-0 left-14 right-0 top-12 z-40 bg-gray-900/20 sm:hidden"
          onClick={close}
        />
      )}
      <aside
        className={`fixed bottom-0 right-0 top-12 z-50 flex w-[min(420px,calc(100vw-3.5rem))] shrink-0 overflow-hidden border-l border-gray-200 bg-white shadow-2xl transition-transform duration-300 sm:static sm:z-auto sm:h-screen sm:shadow-none ${
          isOpen ? 'translate-x-0 sm:translate-x-0' : 'translate-x-full sm:w-0 sm:translate-x-full pointer-events-none'
        }`}
        style={isOpen ? { width: `min(${width}px, calc(100vw - 3.5rem))` } : undefined}
        aria-hidden={!isOpen}
      >
        {/* Drag handle */}
        {isOpen && (
          <div
            onMouseDown={onMouseDown}
            className="hidden w-1 h-full cursor-ew-resize hover:bg-[#3CCED7]/40 active:bg-[#3CCED7]/60 shrink-0 transition-colors sm:block"
          />
        )}

      <div className="flex flex-1 h-full flex-col overflow-hidden min-w-0">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-gray-100 px-3 py-3 sm:px-4">
          <div className="flex min-w-0 items-center gap-2">
            <Bot className="h-4 w-4 shrink-0 text-[#3CCED7]" />
            <span className="truncate text-sm font-semibold text-gray-900">AI Agent</span>
            <span className="hidden shrink-0 rounded-full bg-[#3CCED7]/15 px-1.5 py-0.5 text-[10px] font-semibold text-[#3CCED7] sm:inline">
              AI
            </span>
            {activeProjectId !== null && (
              <TokenBadge projectId={activeProjectId} />
            )}
            <button
              type="button"
              onClick={() => setMessageBoardsOpen((open) => !open)}
              aria-expanded={messageBoardsOpen}
              aria-label={messageBoardsOpen ? 'Hide message boards' : 'Show message boards'}
              title={messageBoardsOpen ? 'Hide message boards' : 'Show message boards'}
              className={`rounded-md p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800 ${
                messageBoardsOpen ? 'bg-gray-100 text-gray-800' : ''
              }`}
            >
              <AgentPanelToggleIcon className="h-4 w-4" />
            </button>
          </div>
          <div className="flex shrink-0 items-center gap-2 sm:gap-3">
            <GenerationOutputsSettings
              sessionId={sessionId}
              approvalRequired={approvalRequired}
              onApprovalChange={(next) => {
                setApprovalRequired(next);
                localStorage.setItem('agent-approval-required-default', String(next));
                if (sessionId) {
                  window.dispatchEvent(
                    new CustomEvent('agent:approval-changed', { detail: { sessionId, value: next } })
                  );
                  window.dispatchEvent(
                    new CustomEvent('agent:session-state', {
                      detail: { sessionId, approvalRequired: next },
                    })
                  );
                }
              }}
            />
            <button
              type="button"
              onClick={() => {
                close();
                router.push('/agent');
              }}
              className="rounded-md p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
              aria-label="Open workspace"
              title="Workspace"
            >
              <Settings className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={close}
              className="rounded-md p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
              aria-label="Close AI Agent panel"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <AgentRegistryStatusBanner />

        {/* Message boards (left) + chat (right) */}
        <div className="flex min-h-0 flex-1 flex-row overflow-hidden">
          <AgentMessageBoardRail
            isVisible={isOpen && messageBoardsOpen}
            activeSessionId={sessionId}
          />
          <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
            <AgentLayoutProvider initialView="overview">
              <AgentChatPage embeddedInFloating />
            </AgentLayoutProvider>
          </div>
        </div>
      </div>
      </aside>
    </>
  );
}
