import { applyMeetingSort, DEFAULT_MEETING_SORT } from '@/lib/meetings/meetingSectionSort';
import type { MeetingListItem } from '@/types/meeting';

const meeting = (partial: Partial<MeetingListItem> & Pick<MeetingListItem, 'id' | 'title'>): MeetingListItem => ({
  summary: '',
  scheduled_date: null,
  status: 'planned',
  meeting_type: 'sync',
  meeting_type_slug: 'sync',
  participants: [],
  tags: [],
  decision_count: 0,
  task_count: 0,
  generated_decisions: [],
  generated_tasks: [],
  related_decisions: [],
  related_tasks: [],
  is_archived: false,
  ...partial,
});

describe('applyMeetingSort', () => {
  const meetings = [
    meeting({ id: 2, title: 'Beta', scheduled_date: '2026-02-01' }),
    meeting({ id: 1, title: 'Alpha', scheduled_date: '2026-01-01' }),
    meeting({ id: 3, title: 'Gamma', scheduled_date: null }),
    meeting({ id: 4, title: 'Delta', scheduled_date: 'not-a-date' }),
  ];

  it('defaults to newest first', () => {
    expect(DEFAULT_MEETING_SORT).toBe('date_desc');
    const sorted = applyMeetingSort(meetings, 'date_desc');
    expect(sorted.map((m) => m.id)).toEqual([2, 1, 3, 4]);
  });

  it('sorts by date ascending and title', () => {
    expect(applyMeetingSort(meetings, 'date_asc').map((m) => m.id)).toEqual([1, 2, 3, 4]);
    expect(applyMeetingSort(meetings, 'title_asc').map((m) => m.title)).toEqual([
      'Alpha',
      'Beta',
      'Delta',
      'Gamma',
    ]);
    expect(applyMeetingSort(meetings, 'title_desc').map((m) => m.title)[0]).toBe('Gamma');
  });

  it('keeps undated meetings after dated ones and is stable by id', () => {
    const undated = [
      meeting({ id: 9, title: 'Z', scheduled_date: null }),
      meeting({ id: 8, title: 'Y', scheduled_date: null }),
    ];
    expect(applyMeetingSort(undated, 'date_asc').map((m) => m.id)).toEqual([8, 9]);
  });
});
