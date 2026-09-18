'use client';

import {
  createContext,
  useContext,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from 'react';

interface DashboardPanelPreferenceContextValue {
  upcomingMeetingsPanelOpen: boolean;
  setUpcomingMeetingsPanelOpen: Dispatch<SetStateAction<boolean>>;
}

const DashboardPanelPreferenceContext = createContext<DashboardPanelPreferenceContextValue | null>(
  null
);

export function DashboardPanelPreferenceProvider({
  children,
  initialUpcomingMeetingsPanelOpen,
}: {
  children: ReactNode;
  initialUpcomingMeetingsPanelOpen: boolean;
}) {
  const [upcomingMeetingsPanelOpen, setUpcomingMeetingsPanelOpen] = useState(
    initialUpcomingMeetingsPanelOpen
  );

  return (
    <DashboardPanelPreferenceContext.Provider
      value={{ upcomingMeetingsPanelOpen, setUpcomingMeetingsPanelOpen }}
    >
      {children}
    </DashboardPanelPreferenceContext.Provider>
  );
}

export function useDashboardPanelPreference(): DashboardPanelPreferenceContextValue {
  const context = useContext(DashboardPanelPreferenceContext);
  if (!context) {  

    // silent fallback for development when no provider is found
    if (process.env.NODE_ENV !== 'production') {  
      console.warn(
        '[DashboardPanelPreference] No provider found. Panel toggles will be ignored. ' +
          'Wrap this route layout in <DashboardPanelPreferenceProvider>.'
      );
    }

    return {
      upcomingMeetingsPanelOpen: true,  
      setUpcomingMeetingsPanelOpen: () => undefined,  
    };
  }
  return context;
}
