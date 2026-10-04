'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Users, Clock, CalendarDays } from 'lucide-react';
import type { MeetingListItem } from '@/types/meeting';
import type { ActionItemDisplay } from '@/types/overview';
import { DashboardTileIcon, useDashboardTileControls } from '@/components/dashboard/builder/DashboardTileControls';

interface MeetingsCardProps {
  upcoming: MeetingListItem[];
  actions: ActionItemDisplay[];
}

const TODAY_ISO = new Date().toISOString().slice(0, 10);

function formatTime(time?: string | null): string {
  if (!time) return '';
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

export default function MeetingsCard({ upcoming, actions }: MeetingsCardProps) {
  const tileControls = useDashboardTileControls('meetings');
  const todayMeetings = upcoming.filter((m) => m.scheduled_date === TODAY_ISO);
  const next7Days = upcoming.filter((m) => m.scheduled_date && m.scheduled_date > TODAY_ISO);

  const openActions = actions.filter((a) => a.converted_task_id === null).slice(0, 5);

  return (
    <Card
      data-overview-card="meetings"
      className="flex h-full min-h-0 flex-col border-[0.5px] border-gray-200 bg-white shadow-none"
    >
      <CardHeader className="shrink-0 pb-2 px-4 pt-4">
        <div className="flex items-center gap-2">
          <DashboardTileIcon icon={<Users className="w-4 h-4 text-gray-400" />} dragHandle={tileControls?.dragHandle} />
          <CardTitle className="text-sm font-medium text-gray-900">Meetings & Action Items</CardTitle>
          <span className="ml-auto text-xs text-gray-400">
            {todayMeetings.length} today
          </span>
          {tileControls?.removeButton}
        </div>
      </CardHeader>

      <CardContent className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pb-4">
        {todayMeetings.length > 0 && (
          <div>
            <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">
              Today
            </div>
            <div className="space-y-1">
              {todayMeetings.map((m) => (
                <div
                  key={m.id}
                  className="flex items-center gap-2 py-1.5 border-b border-gray-50 last:border-0"
                >
                  <Clock className="w-3 h-3 text-gray-400 shrink-0" />
                  <span className="text-[11px] text-gray-500 tabular-nums w-16 shrink-0">
                    {formatTime(m.scheduled_time)}
                  </span>
                  <span className="text-[12px] text-gray-800 truncate flex-1">{m.title}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {next7Days.length > 0 && (
          <div>
            <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">
              Next 7 days
            </div>
            <div className="space-y-1">
              {next7Days.slice(0, 3).map((m) => (
                <div
                  key={m.id}
                  className="flex items-center gap-2 py-1.5 border-b border-gray-50 last:border-0"
                >
                  <CalendarDays className="w-3 h-3 text-gray-400 shrink-0" />
                  <span className="text-[11px] text-gray-500 w-16 shrink-0">
                    {formatDateLabel(m.scheduled_date)}
                  </span>
                  <span className="text-[12px] text-gray-800 truncate flex-1">{m.title}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="pt-2 border-t border-gray-100">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">
              Pending Action Items
            </span>
            <span className="text-[10px] text-gray-400">({openActions.length})</span>
          </div>
          <div className="space-y-1">
            {openActions.length === 0 && (
              <p className="text-xs text-gray-400 py-1">No open action items.</p>
            )}
            {openActions.map((a) => (
              <div
                key={a.id}
                className="flex items-start gap-2 py-1 border-b border-gray-50 last:border-0"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 mt-1.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] text-gray-700 leading-snug truncate">{a.title}</p>
                  <div className="flex items-center gap-2 text-[10px] text-gray-400 mt-0.5">
                    {a.meeting_title && <span className="truncate">{a.meeting_title}</span>}
                    {a.due_date && (
                      <>
                        <span>·</span>
                        <span>Due {formatDateLabel(a.due_date)}</span>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
