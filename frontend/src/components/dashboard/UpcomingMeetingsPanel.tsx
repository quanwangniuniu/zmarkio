'use client';

import { CalendarDays, Clock, Users, X } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import type { MeetingListItem } from '@/types/meeting';

interface UpcomingMeetingsPanelProps {
  meetings: MeetingListItem[];
  isOpen: boolean;
  loading?: boolean;
  onClose?: () => void;
}

const TODAY_ISO = new Date().toISOString().slice(0, 10);

function formatTime(time?: string | null): string {
  if (!time) return '—';
  const [h, m] = time.split(':');
  const hour = Number(h);
  const suffix = hour >= 12 ? 'PM' : 'AM';
  const display = hour % 12 || 12;
  return `${display}:${m} ${suffix}`;
}

function formatDateLabel(iso: string | null): string {
  if (!iso) return 'TBD';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function MeetingRowSkeleton() {
  return (
    <div className="rounded-md border-[0.5px] border-gray-200 p-2.5">
      <div className="flex items-start gap-2">
        <Skeleton className="mt-0.5 h-3.5 w-3.5 rounded-full" />
        <div className="flex-1 space-y-2">
          <div className="flex items-center gap-2">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-4 w-14 rounded-full" />
          </div>
          <Skeleton className="h-4 w-full max-w-[220px]" />
          <Skeleton className="h-3 w-24" />
        </div>
      </div>
    </div>
  );
}

export default function UpcomingMeetingsPanel({
  meetings,
  isOpen,
  loading = false,
  onClose,
}: UpcomingMeetingsPanelProps) {
  const today = meetings.filter((m) => m.scheduled_date === TODAY_ISO);
  const nextSeven = meetings.filter((m) => m.scheduled_date && m.scheduled_date > TODAY_ISO);

  return (
    <aside
      className={`fixed bottom-0 right-0 top-12 z-40 w-[min(360px,calc(100vw-3.5rem))] shrink-0 overflow-hidden border-l-0 bg-white shadow-none transition-transform duration-300 sm:static sm:top-auto sm:z-auto sm:h-full sm:translate-x-0 sm:transition-all ${
        isOpen ? 'translate-x-0 sm:w-[320px] md:border-l md:border-gray-200' : 'pointer-events-none translate-x-full sm:w-0 md:border-l-0'
      }`}
      data-upcoming-meetings-panel
      aria-hidden={!isOpen}
    >
      {!isOpen ? null : (
      <div className="h-full w-full flex flex-col sm:w-[320px]">
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
          <CalendarDays className="w-4 h-4 text-gray-500" />
          <span className="text-sm font-semibold text-gray-900">Upcoming Meetings</span>
          <span className="ml-auto text-[11px] text-gray-400">
            {loading ? <Skeleton className="h-3 w-14" /> : `${today.length} today`}
          </span>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className="-mr-1 inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 sm:hidden"
              aria-label="Close upcoming meetings"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          ) : null}
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
          {loading && (
            <section>
              <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2 px-1">
                <Skeleton className="h-3 w-20" />
              </div>
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, index) => (
                  <MeetingRowSkeleton key={`upcoming-meeting-skeleton-${index}`} />
                ))}
              </div>
            </section>
          )}

          {!loading && meetings.length === 0 && (
            <div className="text-center py-10 text-xs text-gray-400">
              <CalendarDays className="w-6 h-6 mx-auto mb-2 text-gray-300" />
              No upcoming meetings.
            </div>
          )}

          {today.length > 0 && (
            <section>
              <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2 px-1">
                Today
              </div>
              <div className="space-y-2">
                {today.map((m) => (
                  <MeetingRow key={m.id} meeting={m} showDate={false} />
                ))}
              </div>
            </section>
          )}

          {nextSeven.length > 0 && (
            <section>
              <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2 px-1">
                Next 7 days
              </div>
              <div className="space-y-2">
                {nextSeven.map((m) => (
                  <MeetingRow key={m.id} meeting={m} showDate />
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
      )}
    </aside>
  );
}

interface MeetingRowProps {
  meeting: MeetingListItem;
  showDate: boolean;
}

function MeetingRow({ meeting, showDate }: MeetingRowProps) {
  return (
    <button
      className="w-full text-left p-2.5 rounded-md border-[0.5px] border-gray-200 hover:border-[#3CCED7]/40 hover:bg-gray-50 transition-colors group"
    >
      <div className="flex items-start gap-2">
        <div className="flex flex-col items-center pt-0.5 text-gray-400 shrink-0">
          {showDate ? (
            <>
              <CalendarDays className="w-3.5 h-3.5" />
            </>
          ) : (
            <Clock className="w-3.5 h-3.5" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-[11px] text-gray-500 mb-0.5 tabular-nums">
            {showDate ? (
              <span>{formatDateLabel(meeting.scheduled_date)}</span>
            ) : (
              <span>{formatTime(meeting.scheduled_time)}</span>
            )}
            {meeting.meeting_type && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-medium">
                {meeting.meeting_type}
              </span>
            )}
          </div>

          <p className="text-[12px] text-gray-800 font-medium leading-snug line-clamp-2 group-hover:text-[#3CCED7] transition-colors">
            {meeting.title}
          </p>

          <div className="flex items-center gap-3 mt-1 text-[10px] text-gray-400">
            {meeting.participants.length > 0 && (
              <span className="inline-flex items-center gap-1">
                <Users className="w-2.5 h-2.5" />
                {meeting.participants.length}
              </span>
            )}
            {(meeting.decision_count > 0 || meeting.task_count > 0) && (
              <span>
                {meeting.decision_count > 0 && `${meeting.decision_count} decision${meeting.decision_count !== 1 ? 's' : ''}`}
                {meeting.decision_count > 0 && meeting.task_count > 0 && ' · '}
                {meeting.task_count > 0 && `${meeting.task_count} task${meeting.task_count !== 1 ? 's' : ''}`}
              </span>
            )}
          </div>
        </div>
      </div>
    </button>
  );
}
