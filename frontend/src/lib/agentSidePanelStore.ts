import { create } from 'zustand'; 

import {
  AGENT_PANEL_OPENED_EVENT,
  readStoredAgentSessionId,
} from '@/lib/agentLaunchContext';

export interface AgentSidePanelStore { 
  isOpen: boolean;
  toggle: () => void;
  open: () => void;
  close: () => void;
}

type UseAgentSidePanelStore = {
  (): AgentSidePanelStore;
  <T>(selector: (state: AgentSidePanelStore) => T): T;
  getState: () => AgentSidePanelStore;
  setState: (
    partial:
      | AgentSidePanelStore
      | Partial<AgentSidePanelStore>
      | ((state: AgentSidePanelStore) => AgentSidePanelStore | Partial<AgentSidePanelStore>),
    replace?: boolean
  ) => void;
  subscribe: (
    listener: (state: AgentSidePanelStore, prevState: AgentSidePanelStore) => void
  ) => () => void;
  getInitialState: () => AgentSidePanelStore;
};

export const useAgentSidePanelStore = create<AgentSidePanelStore>((set) => ({
  isOpen: false,
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
})) as unknown as UseAgentSidePanelStore; 

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
