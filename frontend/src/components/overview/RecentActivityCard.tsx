'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  ClipboardList,
  Plus,
  CheckCircle2,
  XCircle,
  MessageSquare,
  RefreshCw,
} from 'lucide-react';
import type { ActivityEvent } from '@/types/dashboard';
import { DashboardTileIcon, useDashboardTileControls } from '@/components/dashboard/builder/DashboardTileControls';

interface RecentActivityCardProps {
  activities: ActivityEvent[];
}

const eventIcon: Record<ActivityEvent['event_type'], typeof Plus> = {
  task_created: Plus,
  approved: CheckCircle2,
  rejected: XCircle,
  commented: MessageSquare,
  task_updated: RefreshCw,
};

const eventColor: Record<ActivityEvent['event_type'], string> = {
  task_created: 'text-[#3CCED7] bg-[#3CCED7]/10',
  approved: 'text-emerald-500 bg-emerald-50',
  rejected: 'text-red-500 bg-red-50',
  commented: 'text-gray-500 bg-gray-100',
  task_updated: 'text-amber-500 bg-amber-50',
};

function formatTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'Just now';
  if (min < 60) return `${min}m ago`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function RecentActivityCard({ activities }: RecentActivityCardProps) {
  const tileControls = useDashboardTileControls('activity');
  return (
    <Card
      data-overview-card="activity"
      className="flex h-full min-h-0 flex-col border-[0.5px] border-gray-200 bg-white shadow-none"
    >
      <CardHeader className="shrink-0 pb-2 px-4 pt-4">
        <div className="flex items-center gap-2">
          <DashboardTileIcon icon={<ClipboardList className="w-4 h-4 text-gray-400" />} dragHandle={tileControls?.dragHandle} />
          <CardTitle className="text-sm font-medium text-gray-900">Recent Activity</CardTitle>
          <span className="ml-auto">{tileControls?.removeButton}</span>
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col px-4 pb-4">
        <div className="min-h-0 flex-1 space-y-0 overflow-y-auto">
          {activities.slice(0, 6).map((ev) => {
            const Icon = eventIcon[ev.event_type];
            const colors = eventColor[ev.event_type];
            return (
              <div
                key={ev.id}
                className="flex items-start gap-3 py-2 border-b border-gray-50 last:border-0"
              >
                <div
                  className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${colors}`}
                >
                  <Icon className="w-3 h-3" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] text-gray-700 leading-snug">
                    <span className="font-medium text-gray-900">{ev.user.username}</span>{' '}
                    {ev.human_readable.replace(ev.user.username, '').trim()}
                  </p>
                  {ev.comment_body && (
                    <p className="text-[11px] text-gray-500 italic mt-0.5 line-clamp-1">
                      &ldquo;{ev.comment_body}&rdquo;
                    </p>
                  )}
                  <span className="text-[10px] text-gray-400 mt-0.5 block">
                    {formatTime(ev.timestamp)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        <button className="mt-2 w-full shrink-0 text-center text-[11px] font-medium text-[#3CCED7] hover:underline">
          View all activity
        </button>
      </CardContent>
    </Card>
  );
}
