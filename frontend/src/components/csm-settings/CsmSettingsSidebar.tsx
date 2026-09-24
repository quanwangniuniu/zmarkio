'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { CalendarClock, ClipboardList, FlaskConical, FolderKanban, GitBranch, ListOrdered, Radio, Settings, Shield, Workflow } from 'lucide-react';
import { useBuildUrl } from '@/lib/buildUrl';

const ACTIVE_COLOR = 'text-[#3CCED7]';
const ACTIVE_BAR = 'bg-[#3CCED7]';

function NavLink({
  href,
  label,
  icon,
  isActive,
}: {
  href: string;
  label: string;
  icon: React.ReactNode;
  isActive: boolean;
}) {
  return (
    <Link
      href={href}
      className={`relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
        isActive
          ? ACTIVE_COLOR
          : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
      }`}
    >
      {isActive && (
        <span
          className={`absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full ${ACTIVE_BAR}`}
          aria-hidden
        />
      )}
      <span className={`shrink-0 ${isActive ? ACTIVE_COLOR : 'text-gray-500'}`}>{icon}</span>
      {label}
    </Link>
  );
}

export default function CsmSettingsSidebar() {
  const pathname = usePathname();
  const buildUrl = useBuildUrl();
  const hub = buildUrl('/admin/csm/settings');
  const supportProjects = buildUrl('/admin/csm/settings/support-projects');
  const channels = buildUrl('/admin/csm/settings/channels');
  const workTypes = buildUrl('/admin/csm/settings/work-types');
  const assignments = buildUrl('/admin/csm/settings/assignments');
  const slaPolicy = buildUrl('/admin/csm/settings/sla');
  const businessHours = buildUrl('/admin/csm/settings/business-hours');
  const ticketStatuses = buildUrl('/admin/csm/settings/ticket-statuses');
  const routingRules = buildUrl('/admin/csm/settings/routing-rules');
  const routingSandbox = buildUrl('/admin/csm/settings/routing-sandbox');

  return (
    <aside className="hidden w-[240px] shrink-0 flex-col border-r border-gray-200 bg-white sm:flex">
      <div className="border-b border-gray-200 px-6 py-5">
        <p className="text-sm font-semibold text-gray-900">CSM Settings</p>
      </div>
      <nav className="flex flex-col gap-0.5 p-4">
        <NavLink
          href={hub}
          label="Settings Hub"
          icon={<Settings className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings'}
        />
        <NavLink
          href={supportProjects}
          label="Support Projects"
          icon={<FolderKanban className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/support-projects'}
        />
        <NavLink
          href={channels}
          label="Channels"
          icon={<Radio className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/channels'}
        />
        <NavLink
          href={workTypes}
          label="Work Types"
          icon={<ListOrdered className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/work-types'}
        />
        <NavLink
          href={assignments}
          label="Assignments"
          icon={<ClipboardList className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/assignments'}
        />
        <NavLink
          href={slaPolicy}
          label="SLA Policy"
          icon={<Shield className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/sla'}
        />
        <NavLink
          href={businessHours}
          label="Business Hours"
          icon={<CalendarClock className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/business-hours'}
        />
        <NavLink
          href={ticketStatuses}
          label="Ticket Statuses"
          icon={<Workflow className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/ticket-statuses'}
        />
        <NavLink
          href={routingRules}
          label="Routing Rules"
          icon={<GitBranch className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/routing-rules'}
        />
        <NavLink
          href={routingSandbox}
          label="Sandbox"
          icon={<FlaskConical className="h-4 w-4" aria-hidden />}
          isActive={pathname === '/admin/csm/settings/routing-sandbox'}
        />
      </nav>
    </aside>
  );
}
