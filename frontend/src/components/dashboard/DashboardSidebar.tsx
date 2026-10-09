'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { useRouter, usePathname } from 'next/navigation';
import {
  LayoutDashboard, Megaphone, CheckSquare, GitBranch, Table2,
  Calendar, Users, MessageSquare, Workflow, Clock,
  Bot, ChevronsUpDown, ChevronDown, ChevronRight,
  Target, Mail, Notebook, Facebook, Video, Presentation,
  User as UserIcon, CreditCard, Plug, LogOut, Headset,
  Shield, UserCog, UserCheck, BarChart3, Sparkles, PiggyBank,
  History,
} from 'lucide-react';
import { useProjects } from '@/hooks/useProjects';
import { useAuthStore } from '@/lib/authStore';
import useAuth from '@/hooks/useAuth';
import { useAgentSidePanelStore } from '@/lib/agentSidePanelStore';
import { Skeleton } from '@/components/ui/skeleton';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { useProjectStore } from '@/lib/projectStore';
import {
  isNestedProjectNavActive,
} from '@/lib/projectNestedRoutes';
import { useBuildUrl } from '@/lib/buildUrl';

const getInitials = (name?: string | null): string => {
  if (!name) return '?';
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
};

const humanize = (value: string): string =>
  value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const formatBudgetShort = (value: number | string | null | undefined): string | null => {
  if (value === null || value === undefined || value === '') return null;
  const numeric = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(numeric) || numeric <= 0) return null;
  if (numeric >= 1000) return `$${(numeric / 1000).toFixed(0)}k/mo`;
  return `$${numeric.toFixed(0)}/mo`;
};

type LucideIcon = typeof LayoutDashboard;

interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  children?: NavItem[];
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

/** Opens Agent side panel — not a routable path (deprecated /agent full page). */
const AGENT_PANEL_NAV_HREF = '#agent-panel';

const navGroups: NavGroup[] = [
  {
    title: 'OVERVIEW',
    items: [
      { label: 'Overview', href: '/overview', icon: LayoutDashboard },
      { label: 'Dashboard', href: '/dashboard', icon: BarChart3 },
      { label: 'AI Agent', href: AGENT_PANEL_NAV_HREF, icon: Bot },
    ],
  },
  {
    title: 'MANAGE',
    items: [
      { label: 'Campaigns', href: '/campaigns', icon: Megaphone },
      { label: 'Meta Ads', href: '/meta-ads', icon: BarChart3 },
      { label: 'Tasks', href: '/tasks', icon: CheckSquare },
      { label: 'Decisions', href: '/decisions', icon: GitBranch },
      { label: 'Budget Pools', href: '/budget-pools', icon: PiggyBank },
      { label: 'Spreadsheets', href: '/spreadsheets', icon: Table2 },
    ],
  },
  {
    title: 'CONTENT',
    items: [
      { label: 'Variations Studio', href: '/variations-studio', icon: Sparkles },
      {
        label: 'Ads Draft',
        href: '#',
        icon: Megaphone,
        children: [
          { label: 'Facebook Meta', href: '/facebook-meta', icon: Facebook },
          { label: 'TikTok', href: '/tiktok', icon: Video },
          { label: 'Google Ads', href: '/google-ads', icon: Target },
        ],
      },
      {
        label: 'Email Draft',
        href: '#',
        icon: Mail,
        children: [
          { label: 'Mailchimp', href: '/mailchimp', icon: Mail },
          { label: 'Klaviyo', href: '/klaviyo', icon: Mail },
        ],
      },
      { label: 'Notion', href: '/notion', icon: Notebook },
    ],
  },
  {
    title: 'COLLABORATE',
    items: [
      { label: 'Meetings', href: '/meetings', icon: Users },
      { label: 'Calendar', href: '/calendar', icon: Calendar },
      { label: 'Messages', href: '/messages', icon: MessageSquare },
      { label: 'Miro', href: '/miro', icon: Presentation },
    ],
  },
  {
    title: 'TOOLS',
    items: [
      { label: 'Workflows', href: '/workflows', icon: Workflow },
      { label: 'Timeline', href: '/timeline', icon: Clock },
    ],
  },
];

const adminGroup: NavGroup = {
  title: 'ADMINISTRATION',
  items: [
    { label: 'Members', href: '/select-project', icon: Users },
    { label: 'Roles', href: '/admin/roles', icon: UserCog },
    { label: 'Permissions', href: '/admin/permissions', icon: Shield },
    { label: 'Approvers', href: '/admin/approvers', icon: UserCheck },
    { label: 'Override Audit Log', href: '/admin/override-audits', icon: History },
  ],
};

const isAdminRole = (roles?: unknown): boolean => {
  if (!Array.isArray(roles)) return false;
  return roles.some((r) => {
    if (typeof r !== 'string') return false;
    const lower = r.toLowerCase();
    return lower.includes('admin') || lower.includes('owner');
  });
};

export default function DashboardSidebar() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const pathname = usePathname();
  const [expanded, setExpanded] = useState<string[]>([]);
  const [projectHeaderChecking, setProjectHeaderChecking] = useState(true);
  const { projects, loading, fetchProjects } = useProjects();
  const activeProject = useProjectStore((state) => state.activeProject);
  const hasProjectStoreHydrated = useProjectStore((state) => state.hasHydrated);
  const user = useAuthStore((state) => state.user);
  const authLoading = useAuthStore((state) => state.loading);
  const authInitialized = useAuthStore((state) => state.initialized);
  const { logout } = useAuth();
  const { toggle: toggleAgentPanel, isOpen: isAgentPanelOpen } = useAgentSidePanelStore();

  const handleAgentNav = (href: string) => {
    if (href === AGENT_PANEL_NAV_HREF) {
      toggleAgentPanel();
      return;
    }
    router.push(buildUrl(href));
  };

  const userDisplayName = useMemo(() => {
    if (!user) return null;
    const full = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
    return full || user.username || user.email?.split('@')[0] || 'User';
  }, [user]);

  const userRole = useMemo(() => {
    if (!user) return null;
    const role = Array.isArray(user.roles) ? user.roles[0] : null;
    return role ? humanize(role) : null;
  }, [user]);

  const visibleNavGroups = useMemo(
    () => (isAdminRole(user?.roles) ? [...navGroups, adminGroup] : navGroups),
    [user?.roles],
  );
  const projectHeaderLoading =
    !hasProjectStoreHydrated || loading || (projectHeaderChecking && !activeProject);
  const userCardLoading = !authInitialized || authLoading;

  useEffect(() => {
    if (!hasProjectStoreHydrated) return;
    if (projects.length > 0 || activeProject) {
      setProjectHeaderChecking(false);
      return;
    }

    let cancelled = false;
    setProjectHeaderChecking(true);
    fetchProjects().finally(() => {
      if (!cancelled) setProjectHeaderChecking(false);
    });

    return () => {
      cancelled = true;
    };
  }, [activeProject, hasProjectStoreHydrated, projects.length, fetchProjects]);

  const toggle = (label: string) => {
    setExpanded((prev) =>
      prev.includes(label) ? prev.filter((l) => l !== label) : [...prev, label]
    );
  };

  return (
    <aside className="h-full w-14 shrink-0 flex flex-col border-r border-gray-200 bg-white sm:w-[255px]">
      {/* Logo */}
      <div className="border-b border-gray-100 px-2 py-3 sm:px-4 sm:py-4">
        <Link href="/" className="flex items-center justify-center sm:justify-start">
          <Image
            src="/marketing_simplified_logo.png"
            alt="Marketing Simplified Logo"
            width={36}
            height={36}
            className="h-9 w-9 object-contain sm:hidden"
            priority
          />
          <Image
            src="/marketing_simplified_logo.png"
            alt="Marketing Simplified Logo"
            width={220}
            height={104}
            className="hidden h-20 w-auto sm:block"
            priority
          />
        </Link>
      </div>

      {/* Project header — clickable to switch project */}
      <button
        onClick={() => router.push('/select-project')}
        className="w-full border-b border-gray-100 px-2 py-3 text-left transition-colors hover:bg-gray-50 sm:px-4 group"
        title="Switch project"
        aria-label="Switch project"
      >
        <div className="flex items-center justify-center gap-3 sm:justify-start">
          {projectHeaderLoading ? (
            <Skeleton className="h-8 w-8 rounded-md shrink-0" />
          ) : (
            <div className="w-8 h-8 rounded-md bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0">
              <span className="text-white text-xs font-bold">
                {activeProject ? getInitials(activeProject.name) : '—'}
              </span>
            </div>
          )}
          <div className="hidden min-w-0 flex-1 sm:block">
            {projectHeaderLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-3 w-20" />
              </div>
            ) : activeProject ? (
              <>
                <div className="text-sm font-medium text-gray-900 truncate">
                  {activeProject.name}
                </div>
                <div className="text-xs text-gray-400 truncate">
                  {formatBudgetShort(activeProject.total_monthly_budget) ||
                    activeProject.organization?.name ||
                    'No budget set'}
                </div>
              </>
            ) : (
              <>
                <div className="text-sm font-medium text-gray-900 truncate">
                  {loading ? 'Loading…' : 'Select a project'}
                </div>
                <div className="text-xs text-gray-400 truncate">
                  {loading ? '' : 'No active project'}
                </div>
              </>
            )}
          </div>
          <ChevronsUpDown className="hidden w-4 h-4 text-gray-400 group-hover:text-gray-600 shrink-0 sm:block" />
        </div>
      </button>

      {/* Navigation */}
      <nav className="dashboard-scrollbar flex-1 overflow-y-auto px-1 pb-2 pt-3 sm:px-2 sm:pt-4">
        {visibleNavGroups.map((group, gi) => (
          <div key={group.title} className={gi > 0 ? 'mt-2 sm:mt-5' : ''}>
            <div className="mb-1.5 hidden px-3 text-[11px] font-semibold uppercase tracking-wider text-gray-400 sm:block">
              {group.title}
            </div>
            {group.items.map((item) => {
              const hasChildren = !!item.children?.length;
              const isOpen = hasChildren && expanded.includes(item.label);
              const isAgentPanelItem = item.href === AGENT_PANEL_NAV_HREF;
              const isActive =
                !hasChildren &&
                !isAgentPanelItem &&
                isNestedProjectNavActive(pathname ?? '', item.href, item.href);
              const childActive =
                hasChildren &&
                item.children!.some((c) =>
                  isNestedProjectNavActive(pathname ?? '', c.href, c.href),
                );

              return (
                <div key={item.label}>
                  {hasChildren ? (
                    <>
                      <button
                        onClick={() => toggle(item.label)}
                        className={`relative hidden w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors sm:flex ${
                          childActive
                            ? 'bg-[#3CCED7]/8 text-[#3CCED7]'
                            : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                        }`}
                        aria-expanded={isOpen}
                      >
                        {childActive && (
                          <div className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-[#3CCED7]" />
                        )}
                        <item.icon className="h-[18px] w-[18px] shrink-0" />
                        <span className="flex-1 text-left">{item.label}</span>
                        {isOpen ? (
                          <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />
                        )}
                      </button>

                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            title={item.label}
                            aria-label={item.label}
                            className={`relative flex h-10 w-full items-center justify-center rounded-md text-sm font-medium transition-colors sm:hidden ${
                              childActive
                                ? 'bg-[#3CCED7]/8 text-[#3CCED7]'
                                : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                            }`}
                          >
                            {childActive && (
                              <div className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-[#3CCED7]" />
                            )}
                            <item.icon className="h-[18px] w-[18px] shrink-0" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          side="right"
                          align="start"
                          sideOffset={8}
                          className="w-44 p-1"
                        >
                          <DropdownMenuLabel className="px-2 py-1 text-[11px] font-medium text-gray-500">
                            {item.label}
                          </DropdownMenuLabel>
                          <DropdownMenuSeparator className="my-1" />
                          {item.children!.map((child) => {
                            const childIsActive = isNestedProjectNavActive(
                              pathname ?? '',
                              child.href,
                              child.href,
                            );
                            return (
                              <DropdownMenuItem
                                key={child.href}
                                className={`gap-2 px-2 py-1.5 text-[13px] [&>svg]:size-3.5 ${
                                  childIsActive ? 'text-[#3CCED7]' : ''
                                }`}
                                onSelect={() => handleAgentNav(child.href)}
                              >
                                <child.icon className="text-gray-500" />
                                <span>{child.label}</span>
                              </DropdownMenuItem>
                            );
                          })}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </>
                  ) : (
                    <button
                      onClick={() => {
                        if (item.href === AGENT_PANEL_NAV_HREF) {
                          toggleAgentPanel();
                        } else {
                          router.push(buildUrl(item.href));
                        }
                      }}
                      title={item.label}
                      aria-label={item.label}
                      className={`relative flex h-10 w-full items-center justify-center rounded-md text-sm font-medium transition-colors sm:h-auto sm:justify-start sm:gap-3 sm:px-3 sm:py-2 ${
                        (isAgentPanelItem ? isAgentPanelOpen : isActive)
                          ? 'bg-[#3CCED7]/8 text-[#3CCED7]'
                          : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                      }`}
                    >
                      {(isAgentPanelItem ? isAgentPanelOpen : isActive) && (
                        <div className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-[#3CCED7]" />
                      )}
                      <item.icon className="h-[18px] w-[18px] shrink-0" />
                      <span className="hidden flex-1 text-left sm:block">{item.label}</span>
                    </button>
                  )}

                  {hasChildren && isOpen && (
                    <div className="ml-8 mt-1 mb-1 hidden space-y-0.5 sm:block">
                      {item.children!.map((child) => {
                        const childIsActive = isNestedProjectNavActive(
                          pathname ?? '',
                          child.href,
                          child.href,
                        );
                        return (
                          <button
                            key={child.href}
                            onClick={() => handleAgentNav(child.href)}
                            className={`w-full flex items-center gap-3 px-3 py-1.5 rounded-md text-[13px] transition-colors ${
                              childIsActive
                                ? 'bg-[#3CCED7]/8 text-[#3CCED7] font-medium'
                                : 'text-gray-500 hover:bg-gray-50 hover:text-gray-900'
                            }`}
                          >
                            <child.icon className="w-[14px] h-[14px] shrink-0" />
                            {child.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      {/* User card — opens account menu */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="w-full border-t border-gray-100 px-2 py-3 text-left transition-colors hover:bg-gray-50 focus:bg-gray-50 focus:outline-none sm:px-4"
            title="Account menu"
            aria-label="Account menu"
          >
            <div className="flex items-center justify-center gap-3 sm:justify-start">
              {userCardLoading ? (
                <Skeleton className="h-8 w-8 rounded-full shrink-0" />
              ) : (
                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0">
                  <span className="text-white text-sm font-semibold">
                    {getInitials(userDisplayName)}
                  </span>
                </div>
              )}
              <div className="hidden min-w-0 flex-1 sm:block">
                {userCardLoading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-3 w-32" />
                  </div>
                ) : (
                  <>
                    <div className="text-sm font-medium text-gray-900 truncate">
                      {userDisplayName || 'Not signed in'}
                    </div>
                    <div className="text-xs text-gray-400 truncate">
                      {userRole || user?.email || 'User'}
                    </div>
                  </>
                )}
              </div>
              <ChevronsUpDown className="hidden w-4 h-4 text-gray-400 shrink-0 sm:block" />
            </div>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="center"
          sideOffset={6}
          className="w-[223px] p-1"
        >
          {user?.email && (
            <DropdownMenuLabel className="px-2 py-1 text-[11px] font-normal text-gray-500 truncate">
              {user.email}
            </DropdownMenuLabel>
          )}
          <DropdownMenuSeparator className="my-1" />
          <DropdownMenuItem
            className="text-[13px] px-2 py-1.5 gap-2 [&>svg]:size-3.5"
            onSelect={() => router.push('/profile')}
          >
            <UserIcon className="text-gray-500" />
            <span>Settings</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            className="text-[13px] px-2 py-1.5 gap-2 [&>svg]:size-3.5"
            onSelect={() => router.push(buildUrl('/csm'))}
          >
            <Headset className="text-gray-500" />
            <span>Customer Service</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            className="text-[13px] px-2 py-1.5 gap-2 [&>svg]:size-3.5"
            onSelect={() => router.push('/subscription')}
          >
            <CreditCard className="text-gray-500" />
            <span>Subscription</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            className="text-[13px] px-2 py-1.5 gap-2 [&>svg]:size-3.5"
            onSelect={() => router.push(buildUrl('/integrations'))}
          >
            <Plug className="text-gray-500" />
            <span>Integrations</span>
          </DropdownMenuItem>

          <DropdownMenuSeparator className="my-1" />
          <DropdownMenuItem
            className="text-[13px] px-2 py-1.5 gap-2 [&>svg]:size-3.5 text-red-600 focus:text-red-700 focus:bg-red-50"
            onSelect={async () => {
              await logout();
              router.push('/login');
            }}
          >
            <LogOut />
            <span>Log out</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </aside>
  );
}
