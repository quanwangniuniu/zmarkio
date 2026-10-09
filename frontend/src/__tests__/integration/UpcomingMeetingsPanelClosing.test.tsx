import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import OrgProjectLayout from '@/app/[orgSlug]/[projectSlug]/layout';
import { useDashboardPanelPreference } from '@/components/dashboard/DashboardPanelPreferenceContext';
import { useAuthStore } from '@/lib/authStore';
import { useProjectStore } from '@/lib/projectStore';

jest.mock('next/navigation', () => ({
    useParams: () => ({ orgSlug: 'acme', projectSlug: 'q1-launch' })
}));

jest.mock('@/lib/api/organizationApi', () => ({
  OrganizationAPI: {
    getOrganizationDetail: jest.fn().mockResolvedValue({
      id: 1,
      name: 'Acme',
      slug: 'acme',
    }),
    switchOrganization: jest.fn(),
  },
}));

jest.mock('@/lib/api/projectApi', () => ({
  ProjectAPI: {
    getProject: jest.fn().mockResolvedValue({
      id: 7,
      name: 'Q1 Launch',
      slug: 'q1-launch',
    }),
  },
}));

function PanelStateProbe() {
  const { upcomingMeetingsPanelOpen, setUpcomingMeetingsPanelOpen } =
    useDashboardPanelPreference();
  return (
    <button type="button" onClick={() => setUpcomingMeetingsPanelOpen(false)}>
      {upcomingMeetingsPanelOpen ? 'open' : 'closed'}
    </button>
  );
}

describe('OrgProjectLayout panel preference wiring', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: {
        id: 1,
        email: 'me@test.com',
        current_organization: { id: 1, name: 'Acme', slug: 'acme' },
      } as never,
      hasHydrated: true,
    });
    useProjectStore.setState({
      hasHydrated: true,
    });
  });
  it('provides real panel state to slug-prefixed routes', async () => {
    render(
      <OrgProjectLayout>
        <PanelStateProbe />
      </OrgProjectLayout>
    );
    const probe = await screen.findByRole('button');
    expect(probe).toHaveTextContent('open');
    fireEvent.click(probe);
    await waitFor(() => expect(probe).toHaveTextContent('closed'));
  });
});