import { create } from 'zustand';

import {
  AGENT_PANEL_OPENED_EVENT,
  readStoredAgentSessionId,
} from '@/lib/agentLaunchContext';

interface AgentSidePanelStore {
  isOpen: boolean;
  toggle: () => void;
  open: () => void;
  close: () => void;
}

export const useAgentSidePanelStore = create<AgentSidePanelStore>((set) => ({
  isOpen: false,
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
}));

/** Open the Dashboard Agent side panel (replaces navigating to deprecated /agent). */
export function openAgentSidePanel(): void {
  useAgentSidePanelStore.getState().open();
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new CustomEvent(AGENT_PANEL_OPENED_EVENT));
  const sessionId = readStoredAgentSessionId();
  if (sessionId) {
    window.dispatchEvent(
      new CustomEvent('agent:load-session', { detail: { sessionId } })
    );
  }
}
