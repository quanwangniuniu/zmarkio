import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import RoutingRulesSettingsPage from '@/app/(project)/admin/csm/settings/routing-rules/page';
import { useRoutingRules } from '@/components/csm-settings/routing/useRoutingRules';
import type { RoutingRule } from '@/types/routingRule';
import { VOCABULARY } from '../__mocks__/routingFixtures';

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
    lookups: { vocabulary: VOCABULARY, channelNames: new Map(), organisationNames: new Map() },
  }),
}));

jest.mock('@/components/csm-settings/routing/useRoutingRules', () => ({
  useRoutingRules: jest.fn(),
}));

const rulesState = (rules: RoutingRule[]) => ({
  rules,
  loading: false,
  error: null,
  load: jest.fn(),
  upsert: jest.fn(),
  reorder: jest.fn(),
  toggle: jest.fn(),
  remove: jest.fn(),
});

const rule = (id: number, name: string, overrides: Partial<RoutingRule> = {}): RoutingRule => ({
  id, experience_group: 1, name, position: id, is_enabled: true, match_mode: 'all', conditions: [],
  target_queue: 10, target_queue_name: 'Billing', target_queue_is_active: true, can_route: true,
  add_tags: [], created_at: '', updated_at: '',
  ...overrides,
});

const useRoutingRulesMock = useRoutingRules as jest.Mock;

function loadedGroupId() {
  const { calls } = useRoutingRulesMock.mock;
  return calls[calls.length - 1][1];
}

beforeEach(() => {
  search = '';
  replace.mockClear();
  useRoutingRulesMock.mockReset();
  useRoutingRulesMock.mockImplementation(() => rulesState([]));
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

describe('RoutingRulesSettingsPage rules that cannot route', () => {
  it('flags each rule whose queue is missing or inactive and counts them in a banner', () => {
    useRoutingRulesMock.mockImplementation(() => rulesState([
      rule(1, 'Refunds'),
      rule(2, 'Legacy', { target_queue_is_active: false, can_route: false }),
      rule(3, 'Orphan', { target_queue: null, target_queue_name: null, target_queue_is_active: null, can_route: false }),
    ]));
    render(<RoutingRulesSettingsPage />);

    const banner = screen.getByTestId('unroutable-banner');
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner).toHaveTextContent("2 rules in this group can't route");
    const rows = screen.getAllByTestId('routing-rule-row');
    expect(rows[0]).not.toHaveTextContent("Can't route");
    expect(rows[1]).toHaveTextContent("Can't route");
    expect(rows[1]).toHaveTextContent('Its queue is inactive');
    expect(rows[2]).toHaveTextContent('Its queue was deleted');
  });

  it('shows no banner when every rule can route', () => {
    useRoutingRulesMock.mockImplementation(() => rulesState([rule(1, 'Refunds')]));
    render(<RoutingRulesSettingsPage />);
    expect(screen.queryByTestId('unroutable-banner')).not.toBeInTheDocument();
  });
});
