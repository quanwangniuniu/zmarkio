import { act, renderHook, waitFor } from '@testing-library/react';
import { RoutingSandboxAPI } from '@/lib/api/routingRuleApi';
import CsmConversationAPI from '@/lib/api/csmConversationApi';
import {
  CONFIG_DEBOUNCE_MS,
  PREVIEW_AGENT_NAME,
  useRoutingSandbox,
} from '@/components/csm-settings/sandbox/useRoutingSandbox';
import type { QuickReplyTemplate } from '@/types/csmConversation';
import { makeTrace } from '../__mocks__/routingFixtures';

jest.mock('@/lib/api/routingRuleApi', () => ({
  RoutingSandboxAPI: { evaluate: jest.fn() },
}));

jest.mock('@/lib/api/csmConversationApi', () => ({
  __esModule: true,
  default: { sendMessage: jest.fn(), createTicket: jest.fn() },
}));

const evaluate = RoutingSandboxAPI.evaluate as jest.Mock;

const result = (count: number) => ({
  experience_group: { id: 7, name: 'VIP' },
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
    });
  });

  it("shows the server's reason when evaluation is rejected", async () => {
    evaluate.mockRejectedValueOnce({
      response: { data: { support_channel: ["Channel 'Web chat' is not assigned to experience group 'VIP'."] } },
    });
    const { result: hook } = renderHook(() => useRoutingSandbox(3));
    act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7, supportChannelId: 9 }));
    act(() => hook.current.sendCustomerMessage('Hi'));
    await waitFor(() => expect(hook.current.error).toBe(
      "Channel 'Web chat' is not assigned to experience group 'VIP'.",
    ));
  });

  it('falls back to a generic error without a reason', async () => {
    evaluate.mockRejectedValueOnce(new Error('Network Error'));
    const { result: hook } = renderHook(() => useRoutingSandbox(3));
    act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7 }));
    act(() => hook.current.sendCustomerMessage('Hi'));
    await waitFor(() => expect(hook.current.error).toBe('Could not evaluate routing rules.'));
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
    // Wait out the debounce: an agent bubble must not schedule an evaluation.
    await act(async () => { await new Promise((r) => { setTimeout(r, CONFIG_DEBOUNCE_MS + 50); }); });
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

  describe('timing', () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    const flush = () => act(async () => { jest.runOnlyPendingTimers(); });

    it('sends a message immediately but waits for a burst of settings edits to settle', async () => {
      const { result: hook } = renderHook(() => useRoutingSandbox(3));
      act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7 }));
      act(() => hook.current.sendCustomerMessage('Hi'));
      await act(async () => { jest.advanceTimersByTime(0); });
      expect(evaluate).toHaveBeenCalledTimes(1);

      for (const subject of ['E', 'Er', 'Error']) {
        act(() => hook.current.setConfig({ ...hook.current.config, subject }));
        await act(async () => { jest.advanceTimersByTime(100); });
      }
      expect(evaluate).toHaveBeenCalledTimes(1);
      expect(hook.current.evaluating).toBe(true); // the old trace is marked stale meanwhile

      await act(async () => { jest.advanceTimersByTime(CONFIG_DEBOUNCE_MS); });
      expect(evaluate).toHaveBeenCalledTimes(2);
      expect(evaluate).toHaveBeenLastCalledWith(3, expect.objectContaining({ subject: 'Error' }));
    });

    it('does not evaluate before an experience group is chosen or for blank messages', async () => {
      const { result: hook } = renderHook(() => useRoutingSandbox(3));
      act(() => hook.current.sendCustomerMessage('Hi'));
      act(() => hook.current.sendCustomerMessage('   '));
      await flush();
      expect(evaluate).not.toHaveBeenCalled();
      expect(hook.current.messages).toHaveLength(1);
      expect(hook.current.evaluating).toBe(false);
    });

    it('ignores a slow response that lands while a settings change is waiting', async () => {
      let resolveFirst: (value: unknown) => void = () => {};
      evaluate
        .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
        .mockImplementationOnce(() => Promise.resolve({ ...result(1), rule_count: 2 }));
      const { result: hook } = renderHook(() => useRoutingSandbox(3));
      act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7 }));
      act(() => hook.current.sendCustomerMessage('Hi'));
      await flush(); // first request in flight

      act(() => hook.current.setConfig({ ...hook.current.config, subject: 'Error' }));
      // The old request answers inside the debounce window, before the new one is sent.
      await act(async () => { resolveFirst({ ...result(1), rule_count: 1 }); });
      expect(hook.current.meta).toBeNull();
      expect(hook.current.evaluating).toBe(true);

      await act(async () => { jest.advanceTimersByTime(CONFIG_DEBOUNCE_MS); });
      expect(hook.current.meta?.rule_count).toBe(2);
      expect(hook.current.evaluating).toBe(false);
    });

    it('clears the trace when evaluation fails', async () => {
      const { result: hook } = renderHook(() => useRoutingSandbox(3));
      act(() => hook.current.setConfig({ ...hook.current.config, experienceGroupId: 7 }));
      act(() => hook.current.sendCustomerMessage('Hi'));
      await flush();
      expect(hook.current.traces).toHaveLength(1);

      evaluate.mockRejectedValueOnce(new Error('Network Error'));
      act(() => hook.current.setConfig({ ...hook.current.config, subject: 'x' }));
      await flush();
      expect(hook.current.error).toBe('Could not evaluate routing rules.');
      expect(hook.current.traces).toHaveLength(0);
    });
  });
});
