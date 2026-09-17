import {
  columnIndexToLabel,
  columnLabelToIndex,
  rowColToA1,
  parseA1,
} from '@/lib/spreadsheets/a1';
import { formatTokens } from '@/lib/format';
import { getTaskTypeLabel } from '@/lib/taskTypeLabels';
import { isNetworkError, isRetryableAuthError, LOGIN_ERROR_MESSAGES } from '@/lib/authMessages';
import {
  getTemporaryMuteUntil,
  isParticipantCurrentlyMuted,
  formatMutedUntil,
} from '@/lib/chatMute';
import { limitName, normalizeLimitedName } from '@/lib/messages/nameLimits';
import { getTaskStatusDefinition } from '@/lib/tasks/taskStatuses';
import { getTaskPriorityDefinition } from '@/lib/tasks/taskPriorities';
import { formatTaskDateShort } from '@/lib/tasks/taskDates';
import { dispatchSessionsChanged, AGENT_EVENTS } from '@/lib/agentEvents';
import {
  contentBlocksToCanvasBlocks,
  canvasBlocksToContentBlocks,
} from '@/lib/utils/klaviyoTransform';

describe('a1 helpers', () => {
  it('converts indexes and labels both ways', () => {
    expect(columnIndexToLabel(1)).toBe('A');
    expect(columnIndexToLabel(27)).toBe('AA');
    expect(columnLabelToIndex('A')).toBe(1);
    expect(columnLabelToIndex('AA')).toBe(27);
    expect(columnLabelToIndex('A1')).toBeNull();
    expect(rowColToA1(1, 1)).toBe('A1');
    expect(rowColToA1(0, 1)).toBeNull();
    expect(parseA1('B2')).toEqual({ row: 2, col: 2 });
    expect(parseA1('nope')).toBeNull();
  });
});

describe('format / labels / auth / mute / names', () => {
  it('formats tokens and task type labels', () => {
    expect(formatTokens(null)).toBe('Unlimited');
    expect(formatTokens(500)).toBe('500');
    expect(formatTokens(1500)).toBe('2K');
    expect(formatTokens(2_000_000)).toBe('2M');
    expect(getTaskTypeLabel(null)).toBe('Task');
    expect(getTaskTypeLabel('budget')).toBe('Budget Request');
    expect(getTaskTypeLabel('custom_type')).toBe('Custom Type');
    expect(getTaskTypeLabel('budget', [{ value: 'budget', label: 'API Budget' }])).toBe(
      'API Budget'
    );
  });

  it('classifies auth network/retryable errors', () => {
    expect(LOGIN_ERROR_MESSAGES.GENERIC).toContain('Login failed');
    expect(isNetworkError(null)).toBe(false);
    expect(isNetworkError({ response: null })).toBe(true);
    expect(isNetworkError({ code: 'ERR_NETWORK' })).toBe(true);
    expect(isNetworkError({ message: 'Failed to fetch' })).toBe(true);
    expect(isRetryableAuthError({ response: { status: 500 } })).toBe(true);
    expect(isRetryableAuthError({ response: { status: 401 } })).toBe(false);
  });

  it('computes mute windows and formats until', () => {
    const now = new Date('2026-01-01T10:00:00');
    expect(getTemporaryMuteUntil('1h', now).getHours()).toBe(11);
    expect(getTemporaryMuteUntil('1w', now).getDate()).toBe(8);
    const tomorrow = getTemporaryMuteUntil('tomorrow', now);
    expect(tomorrow.getHours()).toBe(9);

    expect(isParticipantCurrentlyMuted(null)).toBe(false);
    expect(isParticipantCurrentlyMuted({ is_muted: true, muted_until: null })).toBe(true);
    expect(
      isParticipantCurrentlyMuted({
        is_muted: true,
        muted_until: '2026-01-01T12:00:00Z',
      }, new Date('2026-01-01T11:00:00Z'))
    ).toBe(true);
    expect(formatMutedUntil('not-a-date')).toBe('scheduled time');
    expect(formatMutedUntil('2026-01-01T12:00:00Z')).toBeTruthy();
  });

  it('limits names and maps task status/priority/date helpers', () => {
    expect(limitName('abcdef', 3)).toBe('abc');
    expect(normalizeLimitedName('  hi  ', 10)).toBe('hi');
    expect(getTaskStatusDefinition('APPROVED').label).toBe('Approved');
    expect(getTaskStatusDefinition('nope').label).toBe('Draft');
    expect(getTaskPriorityDefinition('HIGH').label).toBe('High');
    expect(getTaskPriorityDefinition(null).label).toBe('Medium');
    expect(formatTaskDateShort(null)).toBe('\u2014');
    expect(formatTaskDateShort('2026-01-15T00:00:00Z')).toMatch(/Jan/);
  });

  it('dispatches agent session events', () => {
    const spy = jest.fn();
    window.addEventListener(AGENT_EVENTS.SESSIONS_CHANGED, spy);
    dispatchSessionsChanged();
    expect(spy).toHaveBeenCalled();
    window.removeEventListener(AGENT_EVENTS.SESSIONS_CHANGED, spy);
  });
});

describe('klaviyoTransform', () => {
  it('converts content blocks to canvas blocks and back', () => {
    const canvas = contentBlocksToCanvasBlocks([
      {
        id: 1,
        block_type: 'Text',
        order: 2,
        content: { section: 'footer', text: 'bye' },
      } as any,
      {
        id: 2,
        block_type: 'Layout',
        order: 1,
        content: JSON.stringify({
          section: 'header',
          columnBlocks: [[{ id: 'c1' }]],
        }),
      } as any,
      {
        id: 3,
        block_type: 'Text',
        order: 3,
        content: '{bad-json',
      } as any,
    ]);

    expect(canvas.header?.length).toBe(1);
    expect(canvas.footer?.length).toBe(1);

    const roundTrip = canvasBlocksToContentBlocks(canvas);
    expect(roundTrip.length).toBeGreaterThan(0);
    expect(roundTrip[0]).toEqual(
      expect.objectContaining({ block_type: expect.any(String), order: expect.any(Number) })
    );
  });
});
