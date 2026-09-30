import { act, renderHook, waitFor } from '@testing-library/react';
import { RoutingSandboxAPI } from '@/lib/api/routingRuleApi';
import CsmConversationAPI from '@/lib/api/csmConversationApi';
import { PREVIEW_AGENT_NAME, useRoutingSandbox } from '@/components/csm-settings/sandbox/useRoutingSandbox';
import type { QuickReplyTemplate } from '@/types/csmConversation';
import { makeTrace } from './__mocks__/routingFixtures';

jest.mock('@/lib/api/routingRuleApi', () => ({
  RoutingSandboxAPI: { evaluate: jest.fn() },
}));

jest.mock('@/lib/api/csmConversationApi', () => ({
  __esModule: true,
  default: { sendMessage: jest.fn(), createTicket: jest.fn() },
}));

const evaluate = RoutingSandboxAPI.evaluate as jest.Mock;

const result = (count: number) => ({
  experience_group: { id: 7, name: 'VIP', status: 'DRAFT' },
  support_channel: null,
  rule_count: 1,
  traces: Array.from({ length: count }, () => makeTrace()),
  warnings: [],
});

const template: QuickReplyTemplate = {
  id: 1, slug: 'refund', organisation: 5, team: null, title: 'Refund', content: 'We will refund you.',
  rich_body: null, tags: ['billing'], is_active: true, created_by: null, created_by_name: null,
  created_at: '', updated_at: '',
};

describe('useRoutingSandbox', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    evaluate.mockImplementation((_projectId, body) => Promise.resolve(result(body.messages.length)));
  });

  it('evaluates every customer message prefix with the scenario config', async () => {
    const { result: hook } = renderHook(() => useRoutingSandbox(3));
    act(() => {
      hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7, supportChannelId: 9, subject: 'Help' });
    });
    act(() => hook.current.sendCustomerMessage('Hi'));
    await waitFor(() => expect(hook.current.traces).toHaveLength(1));
    act(() => hook.current.sendCustomerMessage('  I want a refund  '));
    await waitFor(() => expect(hook.current.traces).toHaveLength(2));

    expect(evaluate).toHaveBeenLastCalledWith(3, {
      experience_group: 7,
      messages: ['Hi', 'I want a refund'],
      subject: 'Help',
      support_channel: 9,
      customer_organisation: null,
      simulated_at: null,
      evaluate_each_prefix: true,
    });
  });

  it('does not evaluate before an experience group is chosen or for blank messages', () => {
    const { result: hook } = renderHook(() => useRoutingSandbox(3));
    act(() => hook.current.sendCustomerMessage('Hi'));
    act(() => hook.current.sendCustomerMessage('   '));
    expect(evaluate).not.toHaveBeenCalled();
    expect(hook.current.messages).toHaveLength(1);
  });

  it('inserts templates as local preview bubbles without calling any API', async () => {
    const { result: hook } = renderHook(() => useRoutingSandbox(3));
    act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7 }));
    act(() => hook.current.sendCustomerMessage('refund'));
    await waitFor(() => expect(evaluate).toHaveBeenCalledTimes(1));

    act(() => hook.current.insertTemplate(template));

    const last = hook.current.messages[hook.current.messages.length - 1];
    expect(last.sender_type).toBe('agent');
    expect(last.sender_agent_name).toBe(PREVIEW_AGENT_NAME);
    expect(last.content).toBe('We will refund you.');
    expect(evaluate).toHaveBeenCalledTimes(1);
    expect(CsmConversationAPI.sendMessage).not.toHaveBeenCalled();
  });

  it('reset clears the simulated conversation', async () => {
    const { result: hook } = renderHook(() => useRoutingSandbox(3));
    act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7 }));
    act(() => hook.current.sendCustomerMessage('refund'));
    await waitFor(() => expect(hook.current.traces).toHaveLength(1));
    act(() => hook.current.reset());
    expect(hook.current.messages).toEqual([]);
    expect(hook.current.traces).toEqual([]);
  });

  it('re-runs when the window regains focus so rule edits elsewhere show up', async () => {
    const { result: hook } = renderHook(() => useRoutingSandbox(3));
    act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7 }));
    act(() => hook.current.sendCustomerMessage('refund'));
    await waitFor(() => expect(evaluate).toHaveBeenCalledTimes(1));
    act(() => { window.dispatchEvent(new Event('focus')); });
    await waitFor(() => expect(evaluate).toHaveBeenCalledTimes(2));
  });
});
