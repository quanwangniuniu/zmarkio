import {
  CalendarAPI,
  derivedEventToEventDTO,
  extractUserDescription,
  extractNavigationMetadata,
  type DerivedCalendarEventDTO,
} from '@/lib/api/calendarApi';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));

import api from '@/lib/api';

const mockApi = api as jest.Mocked<typeof api>;

describe('CalendarAPI', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.get.mockResolvedValue({ data: [] } as any);
    mockApi.post.mockResolvedValue({ data: {} } as any);
    mockApi.patch.mockResolvedValue({ data: {} } as any);
    mockApi.delete.mockResolvedValue({ data: {} } as any);
  });

  it('scopes list/create/subscription helpers', async () => {
    await CalendarAPI.listCalendars(12);
    expect(mockApi.get).toHaveBeenCalledWith('/api/calendars/', {
      params: { project_id: 12 },
    });
    await CalendarAPI.listCalendars();
    expect(mockApi.get).toHaveBeenCalledWith('/api/calendars/', {});

    await CalendarAPI.createCalendar({ name: 'Main' });
    await CalendarAPI.listSubscriptions();
    await CalendarAPI.updateSubscription('sub-1', { is_hidden: true });
  });

  it('builds view and event endpoints with optional params', async () => {
    await CalendarAPI.getDayView({
      date: '2026-01-01',
      calendar_ids: ['a', 'b'],
      project_id: 1,
    });
    await CalendarAPI.getWeekView({ start_date: '2026-01-01' });
    await CalendarAPI.getMonthView({ year: 2026, month: 1, project_id: 2 });
    await CalendarAPI.getAgendaView({
      start_date: '2026-01-01',
      end_date: '2026-01-07',
      calendar_ids: ['a'],
    });
    await CalendarAPI.createEvent({ title: 'E', start_datetime: 'x', end_datetime: 'y', is_all_day: false, is_recurring: false });
    await CalendarAPI.updateEvent('e1', { title: 'E2' });
    await CalendarAPI.updateEventInstance('e1', '2026-01-01T00:00:00Z', { title: 'E3' });
    await CalendarAPI.splitEventSeries('e1', '2026-01-01T00:00:00Z', { title: 'E4' });
    await CalendarAPI.deleteEvent('e1');
    await CalendarAPI.getDerivedEvents({ start: 'a', end: 'b', project_id: 3 });
    expect(mockApi.get).toHaveBeenCalled();
    expect(mockApi.post).toHaveBeenCalled();
    expect(mockApi.patch).toHaveBeenCalled();
    expect(mockApi.delete).toHaveBeenCalled();
  });
});

describe('derived calendar helpers', () => {
  const base: DerivedCalendarEventDTO = {
    id: 9,
    event_type: 'decision',
    title: 'Decide',
    description: 'User facing',
    start_time: '2026-01-01T00:00:00.000Z',
    end_time: '2026-01-01T23:59:00.000Z',
    decision_id: 1,
    task_id: null,
    decision_slug: 'd',
    task_slug: null,
    review_id: null,
    project_id: 4,
  };

  it('maps derived events including all-day and missing end_time', () => {
    const allDay = derivedEventToEventDTO(base);
    expect(allDay.id).toBe('derived-9');
    expect(allDay.is_all_day).toBe(true);
    expect(allDay.color).toBe('#8B5CF6');

    const timed = derivedEventToEventDTO({
      ...base,
      event_type: 'task',
      start_time: '2026-01-01T10:00:00.000Z',
      end_time: null,
    });
    expect(timed.is_all_day).toBe(false);
    expect(timed.end_datetime).toBeTruthy();
    expect(timed.color).toBe('#10B981');

    const review = derivedEventToEventDTO({
      ...base,
      event_type: 'decision_review',
      start_time: '2026-01-01T00:00:00.000Z',
      end_time: '2026-01-02T00:00:00.000Z',
    });
    expect(review.color).toBe('#F59E0B');
  });

  it('extracts description and metadata', () => {
    const dto = derivedEventToEventDTO(base);
    expect(extractUserDescription(dto.description || '')).toBe('User facing');
    expect(extractNavigationMetadata(dto.description || '')).toEqual(
      expect.objectContaining({ isDerived: true, decision_id: 1 })
    );
    expect(extractUserDescription('')).toBe('');
    expect(extractNavigationMetadata('')).toBeNull();
    expect(extractNavigationMetadata('{"ok":true}')).toEqual({ ok: true });
    expect(extractNavigationMetadata('not-json')).toBeNull();
  });
});
