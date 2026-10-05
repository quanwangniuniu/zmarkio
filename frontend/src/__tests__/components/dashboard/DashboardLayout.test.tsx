import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { DashboardPanelPreferenceProvider } from '@/components/dashboard/DashboardPanelPreferenceContext';

jest.mock('@/components/dashboard/DashboardSidebar', () => ({
  __esModule: true,
  default: () => <div data-testid="dashboard-sidebar" />,
}));

jest.mock('@/components/dashboard/NotificationBell', () => ({
  __esModule: true,
  default: () => <div data-testid="notification-bell" />,
}));

jest.mock('@/components/agent/AgentSidePanel', () => ({
  __esModule: true,
  default: () => <div data-testid="agent-side-panel" />,
}));

jest.mock('@/lib/api/authApi', () => ({ authApi: { getCurrentUser: jest.fn().mockRejectedValue(new Error('No user in layout test')) } }));

jest.mock('@/lib/projectStore', () => ({
  useProjectStore: (selector: (state: { activeProject: null; hasHydrated: boolean }) => unknown) =>
    selector({ activeProject: null, hasHydrated: true }),
}));

jest.mock('@/lib/api/meetingsApi', () => ({
  MeetingsAPI: {
    listMeetingsPaginated: jest.fn(),
  },
}));

const mockMatchMedia = (matches: boolean) => {
  window.matchMedia = jest.fn().mockImplementation((query) => ({
    matches,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  }));
};

describe('DashboardLayout upcoming meetings panel preference', () => {
  beforeEach(() => {
    localStorage.clear();
    mockMatchMedia(false);
  });

  it('initializes the UpcomingMeetingsPanel from localStorage and persists user toggles', async () => {
    localStorage.setItem('dashboard-upcoming-meetings-panel-open', 'false');

    const { container } = render(
      <DashboardPanelPreferenceProvider initialUpcomingMeetingsPanelOpen>
        <DashboardLayout>
          <div>Page content</div>
        </DashboardLayout>
      </DashboardPanelPreferenceProvider>
    );

    await waitFor(() => expect(container.querySelector('[data-upcoming-meetings-panel]')).toBeNull());
    expect(screen.getByRole('button', { name: /show panel/i })).toBeInTheDocument();
    expect(screen.queryByText('Upcoming Meetings')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /show panel/i }));

    expect(container.querySelector('[data-upcoming-meetings-panel]')).toHaveClass('sm:w-[320px]');
    expect(localStorage.getItem('dashboard-upcoming-meetings-panel-open')).toBe('true');
    expect(screen.getByRole('button', { name: /hide panel/i })).toBeInTheDocument();
    expect(screen.getByText('Upcoming Meetings')).toBeInTheDocument();
  });

  it('ignores persisted open state on mobile and does not persist mobile toggles', async () => {
    mockMatchMedia(true);
    localStorage.setItem('dashboard-upcoming-meetings-panel-open', 'true');

    const { container } = render(
      <DashboardPanelPreferenceProvider initialUpcomingMeetingsPanelOpen>
        <DashboardLayout>
          <div>Page content</div>
        </DashboardLayout>
      </DashboardPanelPreferenceProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /show panel/i })).toBeInTheDocument();
    });

    expect(container.querySelector('[data-upcoming-meetings-panel]')).toBeNull();
    expect(screen.queryByText('Upcoming Meetings')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /show panel/i }));

    expect(container.querySelector('[data-upcoming-meetings-panel]')).toHaveClass('translate-x-0');
    expect(screen.getByText('Upcoming Meetings')).toBeInTheDocument();
    expect(localStorage.getItem('dashboard-upcoming-meetings-panel-open')).toBe('true');
  });
  it('hides and reopens the panel even on routes without a preference provider', async () => {
    const { container } = render(<DashboardLayout><div>Page content</div></DashboardLayout>);
    await waitFor(() => expect(screen.getByRole('button', { name: /hide panel/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /hide panel/i }));
    expect(container.querySelector('[data-upcoming-meetings-panel]')).toBeNull();
    expect(localStorage.getItem('dashboard-upcoming-meetings-panel-open')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: /show panel/i }));
    expect(screen.getByText('Upcoming Meetings')).toBeInTheDocument();
  });

});
