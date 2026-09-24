import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import RoutingRuleFormDrawer from '@/components/csm-settings/routing/RoutingRuleFormDrawer';
import { RoutingRuleAPI } from '@/lib/api/routingRuleApi';
import type { Queue } from '@/types/csm';
import type { RoutingRule } from '@/types/routingRule';
import { VOCABULARY } from './__mocks__/routingFixtures';

jest.mock('@/lib/api/routingRuleApi', () => ({
  RoutingRuleAPI: { create: jest.fn(), update: jest.fn() },
}));

const create = RoutingRuleAPI.create as jest.Mock;
const update = RoutingRuleAPI.update as jest.Mock;

const QUEUES = [
  { id: 10, name: 'Billing', is_active: true },
  { id: 11, name: 'Archived', is_active: false },
] as Queue[];

async function selectPortalOption(label: RegExp, optionName: string) {
  fireEvent.click(screen.getByLabelText(label));
  await waitFor(() => expect(screen.getByRole('option', { name: optionName })).toBeInTheDocument());
  fireEvent.click(screen.getByRole('option', { name: optionName }));
}

function renderDrawer(editing: RoutingRule | null = null) {
  const onSaved = jest.fn();
  render(
    <RoutingRuleFormDrawer
      isOpen
      projectId={1}
      experienceGroupId={7}
      editing={editing}
      vocabulary={VOCABULARY}
      queues={QUEUES}
      channels={[]}
      organisations={[]}
      onClose={jest.fn()}
      onSaved={onSaved}
    />,
  );
  return { onSaved };
}

describe('RoutingRuleFormDrawer', () => {
  beforeEach(() => jest.clearAllMocks());

  it('builds a rule payload from the vocabulary-driven editor', async () => {
    create.mockResolvedValue({ id: 99 });
    const { onSaved } = renderDrawer();

    fireEvent.change(screen.getByLabelText(/rule name/i), { target: { value: 'Refunds' } });
    const keywordInput = screen.getByPlaceholderText(/type a keyword/i);
    fireEvent.change(keywordInput, { target: { value: 'refund' } });
    fireEvent.keyDown(keywordInput, { key: 'Enter' });
    await selectPortalOption(/route to queue/i, 'Billing');
    fireEvent.click(screen.getByRole('button', { name: /create rule/i }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create).toHaveBeenCalledWith(1, {
      experience_group: 7,
      name: 'Refunds',
      is_enabled: true,
      match_mode: 'all',
      conditions: [{ field: 'latest_message', operator: 'contains_any', value: ['refund'] }],
      target_queue: 10,
      add_tags: [],
    });
    expect(onSaved).toHaveBeenCalledWith({ id: 99 });
  });

  it('only offers active queues', async () => {
    renderDrawer();
    fireEvent.click(screen.getByLabelText(/route to queue/i));
    await waitFor(() => expect(screen.getByRole('option', { name: 'Billing' })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: 'Archived' })).not.toBeInTheDocument();
  });

  it('requires a name and queue before calling the API', async () => {
    renderDrawer();
    fireEvent.click(screen.getByRole('button', { name: /create rule/i }));
    expect(await screen.findByText('Name is required.')).toBeInTheDocument();
    expect(screen.getByText('Choose a queue to route to.')).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it('shows per-condition server errors', async () => {
    create.mockRejectedValue({
      response: { data: { conditions: ['Condition 1: Provide at least one keyword.'] } },
    });
    renderDrawer();
    fireEvent.change(screen.getByLabelText(/rule name/i), { target: { value: 'Refunds' } });
    await selectPortalOption(/route to queue/i, 'Billing');
    fireEvent.click(screen.getByRole('button', { name: /create rule/i }));
    expect(await screen.findByTestId('condition-errors')).toHaveTextContent(
      'Condition 1: Provide at least one keyword.',
    );
  });

  it('adds and removes condition rows, and edits an existing rule', async () => {
    update.mockResolvedValue({ id: 5 });
    renderDrawer({
      id: 5, experience_group: 7, name: 'Busy', position: 0, is_enabled: false, match_mode: 'any',
      conditions: [{ field: 'message_count', operator: 'gte', value: 3 }],
      action_type: 'route_to_queue', target_queue: 10, target_queue_name: 'Billing',
      target_queue_is_active: true, add_tags: ['vip'], created_at: '', updated_at: '',
    });
    expect(screen.getAllByTestId('condition-row')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: /add condition/i }));
    expect(screen.getAllByTestId('condition-row')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Remove condition 2' }));

    fireEvent.click(screen.getByRole('button', { name: /save rule/i }));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update).toHaveBeenCalledWith(5, expect.objectContaining({
      is_enabled: false,
      match_mode: 'any',
      conditions: [{ field: 'message_count', operator: 'gte', value: 3 }],
      add_tags: ['vip'],
    }));
  });
});
