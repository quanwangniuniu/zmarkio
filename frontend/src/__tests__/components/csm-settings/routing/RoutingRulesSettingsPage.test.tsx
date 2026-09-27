import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import RoutingRulesSettingsPage from '@/app/(project)/admin/csm/settings/routing-rules/page';
import { useRoutingRules } from '@/components/csm-settings/routing/useRoutingRules';
import { VOCABULARY } from './__mocks__/routingFixtures';

const replace = jest.fn();
let search = '';

jest.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(search),
}));

jest.mock('@/lib/buildUrl', () => ({ useBuildUrl: () => (path: string) => path }));

jest.mock('@/components/csm-settings/useProjectIdFromUrl', () => ({
  useProjectIdFromUrl: () => ({ projectId: 1, projectValid: true }),
}));

jest.mock('@/components/csm-settings/routing/useRoutingOptions', () => ({
  useRoutingOptions: () => ({
    loading: false,
    error: null,
    vocabulary: VOCABULARY,
    // Newest first, as the experience group API orders them.
    experienceGroups: [
      { id: 2, name: 'Standard Customers' },
      { id: 1, name: 'VIP Customers' },
    ],
    queues: [],
    channels: [],
    organisations: [],
  }),
}));

jest.mock('@/components/csm-settings/routing/useRoutingRules', () => ({
  useRoutingRules: jest.fn(() => ({
    rules: [],
    loading: false,
    error: null,
    load: jest.fn(),
    upsert: jest.fn(),
    reorder: jest.fn(),
    toggle: jest.fn(),
    remove: jest.fn(),
  })),
}));

const useRoutingRulesMock = useRoutingRules as jest.Mock;

function loadedGroupId() {
  const { calls } = useRoutingRulesMock.mock;
  return calls[calls.length - 1][1];
}

beforeEach(() => {
  search = '';
  replace.mockClear();
  useRoutingRulesMock.mockClear();
});

describe('RoutingRulesSettingsPage group selection', () => {
  it('defaults to the first group when the URL names none', () => {
    render(<RoutingRulesSettingsPage />);
    expect(loadedGroupId()).toBe(2);
  });

  it('keeps the group from ?group= across a reload', () => {
    search = 'group=1';
    render(<RoutingRulesSettingsPage />);
    expect(loadedGroupId()).toBe(1);
    expect(screen.getByLabelText(/experience group/i)).toHaveTextContent('VIP Customers');
  });

  it('falls back to the first group for an unknown id', () => {
    search = 'group=999';
    render(<RoutingRulesSettingsPage />);
    expect(loadedGroupId()).toBe(2);
  });

  it('writes the chosen group to the URL', async () => {
    render(<RoutingRulesSettingsPage />);
    fireEvent.click(screen.getByLabelText(/experience group/i));
    await waitFor(() => expect(screen.getByRole('option', { name: 'VIP Customers' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('option', { name: 'VIP Customers' }));
    expect(replace).toHaveBeenCalledWith('/admin/csm/settings/routing-rules?group=1');
  });
});
