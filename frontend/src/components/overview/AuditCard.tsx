'use client';

import { ShieldCheck, UserCog, Users, FolderOpen, Building2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import type { AdminAuditEvent } from "@/types/audit";
import { useRouter } from "next/navigation";
import { DashboardTileIcon, useDashboardTileControls } from '@/components/dashboard/builder/DashboardTileControls';

const ACTION_LABEL: Record<string, string> = {
  'role.created':             'created role',
  'role.updated':             'updated role',
  'role.deleted':             'deleted role',
  'role.permissions_updated': 'updated permissions for',
  'role.permissions_copied':  'copied permissions to',
  'user_role.assigned':       'assigned role to',
  'user_role.removed':        'removed role from',
  'project.updated':          'updated project',
  'project.deleted':          'deleted project',
  'project.labels_updated':   'updated labels for',
  'org.slug_updated':         'updated org slug',
  'org.deleted':              'deleted org',
  'org.admin_assigned':       'assigned org admin',
  'org.admin_removed':        'removed org admin',
};

function actionIcon(action: string) {
  if (action.startsWith('role.')) return { Icon: UserCog, colors: 'text-purple-500 bg-purple-50' };
  if (action.startsWith('user_role.')) return { Icon: Users, colors: 'text-blue-500 bg-blue-50' };
  if (action.startsWith('project.')) return { Icon: FolderOpen, colors: 'text-teal-500 bg-teal-50' };
  return { Icon: Building2, colors: 'text-amber-500 bg-amber-50' };
}

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

export default function AuditCard({ events }: { events: AdminAuditEvent[] }) {
  const router = useRouter();
  const tileControls = useDashboardTileControls('audit');
  return (
    <Card data-overview-card="audit" className="flex h-full min-h-0 flex-col border-[0.5px] border-gray-200 bg-white shadow-none">
      <CardHeader className="shrink-0 pb-2 px-4 pt-4">
        <div className="flex items-center gap-2">
          <DashboardTileIcon icon={<ShieldCheck className="w-4 h-4 text-gray-400" />} dragHandle={tileControls?.dragHandle} />
          <CardTitle className="text-sm font-medium text-gray-900">Admin Action Log</CardTitle>
          <span className="ml-auto">{tileControls?.removeButton}</span>
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col px-4 pb-4">
        {events.length === 0 ? (
          <p className="min-h-0 flex-1 overflow-y-auto py-2 text-[12px] text-gray-400">No recent admin actions.</p>
        ) : (
          <div className="min-h-0 flex-1 space-y-0 overflow-y-auto">
            {events.slice(0, 6).map((ev) => {
              const { Icon, colors } = actionIcon(ev.action);
              const label = ACTION_LABEL[ev.action] ?? ev.action;
              const actor = ev.actor_name || ev.actor_email || 'Unknown';
              return (
                <div
                  key={ev.id}
                  className="flex items-start gap-3 py-2 border-b border-gray-50 last:border-0"
                >
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${colors}`}>
                    <Icon className="w-3 h-3" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[12px] text-gray-700 leading-snug">
                      <span className="font-medium text-gray-900">{actor}</span>{' '}
                      {label}
                      {ev.target_name && (
                        <span className="font-medium text-gray-900"> {ev.target_name}</span>
                      )}
                    </p>
                    <span className="text-[10px] text-gray-400 mt-0.5 block">
                      {formatTime(ev.timestamp)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <button
          onClick={() => router.push('/admin/audit-log')}
          className="mt-2 w-full shrink-0 text-center text-[11px] font-medium text-[#3CCED7] hover:underline"
        >
          View all actions →
        </button>
      </CardContent>
    </Card>
  );
}
