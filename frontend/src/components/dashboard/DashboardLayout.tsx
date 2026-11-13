'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { AlertTriangle, ArrowLeft, PanelRightClose, PanelRightOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import DashboardSidebar from './DashboardSidebar';
import NotificationBell from './NotificationBell';
import UpcomingMeetingsPanel from './UpcomingMeetingsPanel';
import AgentSidePanel from '@/components/agent/AgentSidePanel';
import QuickStartPostCreateChecklist from '@/components/quick-start/QuickStartPostCreateChecklist';
import { useDashboardPanelPreference } from './DashboardPanelPreferenceContext';
import { useProjectStore } from '@/lib/projectStore';
import { MeetingsAPI } from '@/lib/api/meetingsApi';
import { splitMeetingRowsBySchedule } from '@/lib/meetings/meetingScheduleSplit';
import {
  UPCOMING_MEETINGS_PANEL_STORAGE_KEY,
  normalizeUpcomingMeetingsPanelOpen,
} from '@/lib/dashboardPanelPreferences';
import type { AlertData } from '@/lib/mock/dashboardMock';
import type { MeetingListItem } from '@/types/meeting';
import { NotificationDrawerProvider } from '@/components/notifications/NotificationDrawerProvider';
import NotificationDrawer from '@/components/notifications/NotificationDrawer';
import { useNotificationSSE } from '@/hooks/useNotificationSSE';
import { useStripProjectIdFromUrl } from '@/lib/useStripProjectIdFromUrl';
import { useGuardedRouterPush } from '@/contexts/UnsavedChangesGuardContext';
import { useBuildUrl } from '@/lib/buildUrl';
import { authApi } from '@/lib/api/authApi';
import { useAuthStore } from '@/lib/authStore';
import type { PasswordRotationStatus } from '@/types/auth';

interface DashboardLayoutProps {
  children: React.ReactNode;
  alerts?: AlertData[];
  upcomingMeetings?: MeetingListItem[];
  hideRightPanel?: boolean;
  mainClassName?: string;
}

const humanize = (value: string): string =>
  value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const BREADCRUMB_ROOT: Record<string, string> = {
  'select-project': 'Projects',
  overview: 'Dashboard',
  campaigns: 'Manage',
  tasks: 'Manage',
  decisions: 'Manage',
  spreadsheet: 'Manage',
  spreadsheets: 'Manage',
  meetings: 'Collaborate',
  calendar: 'Collaborate',
  messages: 'Collaborate',
  miro: 'Collaborate',
  'variations-studio': 'Content',
  facebook_meta: 'Content',
  tiktok: 'Content',
  google_ads: 'Content',
  mailchimp: 'Content',
  'mailchimp-v2': 'Content',
  klaviyo: 'Content',
  'klaviyo-v2': 'Content',
  notion: 'Content',
  workflows: 'Tools',
  timeline: 'Tools',
  settings: 'Tools',
  agent: 'Overview',
  profile: 'Account',
  organizations: 'Profile',
  csm: 'Service',
};

const BREADCRUMB_LEAF: Record<string, string> = {
  'select-project': 'Select Project',
  overview: 'Overview',
  spreadsheet: 'Spreadsheets',
  'mailchimp-v2': 'Mailchimp',
  'klaviyo-v2': 'Klaviyo',
  organizations: 'Organizations',
  csm: 'Customer Service',
};

const getBreadcrumb = (pathname: string | null): { root: string; leaf: string } => {
  const segments = (pathname || '').split('/').filter(Boolean);
  const first = segments[0] || 'overview';
  const root = BREADCRUMB_ROOT[first] || 'Dashboard';
  const leaf = BREADCRUMB_LEAF[first] || humanize(first);
  return { root, leaf };
};

const ROOT_PATHS = new Set([
  '/overview',
  '/select-project',
  '/mailchimp',
  '/klaviyo',
  '/notion',
  '/messages',
  '/profile',
  '/subscription',
  '/integrations',
  '/workflows',
  '/timeline',
]);

const MOBILE_VIEWPORT_QUERY = '(max-width: 639px)';

const isMobileViewport = (): boolean =>
  typeof window !== 'undefined' && window.matchMedia(MOBILE_VIEWPORT_QUERY).matches;

const persistUpcomingMeetingsPanelPreference = (next: boolean) => {
  localStorage.setItem(UPCOMING_MEETINGS_PANEL_STORAGE_KEY, String(next));
  document.cookie = `${UPCOMING_MEETINGS_PANEL_STORAGE_KEY}=${String(next)}; path=/; max-age=31536000; samesite=lax`;
};

export default function DashboardLayout({
  children,
  alerts = [],
  upcomingMeetings,
  hideRightPanel = false,
  mainClassName = '',
}: DashboardLayoutProps) {
  // Establish the SSE connection for real-time notification push.
  useNotificationSSE();
  useStripProjectIdFromUrl();

  const {
    upcomingMeetingsPanelOpen: isPanelOpen,
    setUpcomingMeetingsPanelOpen: setIsPanelOpen,
  } = useDashboardPanelPreference();
  const [meetingsLoading, setMeetingsLoading] = useState(
    () => !(upcomingMeetings && upcomingMeetings.length > 0)
  );
  const pathname = usePathname();
  const router = useRouter();
  const guardedPush = useGuardedRouterPush(router.push);
  const buildUrl = useBuildUrl();
  const searchParams = useSearchParams();
  const breadcrumb = useMemo(() => getBreadcrumb(pathname), [pathname]);
  const showBack = !!pathname && !ROOT_PATHS.has(pathname);
  const handleBack = () => {
    const segments = (pathname ?? '').split('/').filter(Boolean);
    const parent = segments.length > 1 ? '/' + segments.slice(0, -1).join('/') : '/overview';
    // /admin maps to Django admin — navigate to project selection instead
    // /admin/csm/* maps back to the CSM page
    const safePath =
      parent === '/admin' ? '/select-project' :
      parent === '/admin/csm' ? buildUrl('/csm') :
      parent === '/organizations' ? '/profile' :
      buildUrl(parent);
    // Preserve existing query params (e.g. ?project=1) when navigating within admin settings
    const qs = searchParams.toString();
    guardedPush(qs ? `${safePath}?${qs}` : safePath);
  };
  const activeProject = useProjectStore((s) => s.activeProject);
  const hasProjectStoreHydrated = useProjectStore((s) => s.hasHydrated);
  const setAuthUser = useAuthStore((s) => s.setUser);
  const authUser = useAuthStore((s) => s.user);
  const [passwordRotation, setPasswordRotation] = useState<PasswordRotationStatus | null>(null);
  const [autoMeetings, setAutoMeetings] = useState<MeetingListItem[]>([]);
  const useExplicit = upcomingMeetings && upcomingMeetings.length > 0;

  useEffect(() => {
    const mediaQuery = window.matchMedia(MOBILE_VIEWPORT_QUERY);
    const syncPanelStateForViewport = () => {
      if (mediaQuery.matches) {
        setIsPanelOpen(false);
        return;
      }
      const stored = localStorage.getItem(UPCOMING_MEETINGS_PANEL_STORAGE_KEY);
      if (stored === 'false' || stored === 'true') {
        const storedOpen = normalizeUpcomingMeetingsPanelOpen(stored);
        setIsPanelOpen(storedOpen);
        document.cookie = `${UPCOMING_MEETINGS_PANEL_STORAGE_KEY}=${String(storedOpen)}; path=/; max-age=31536000; samesite=lax`;
      }
    };

    syncPanelStateForViewport();
    mediaQuery.addEventListener('change', syncPanelStateForViewport);

    return () => {
      mediaQuery.removeEventListener('change', syncPanelStateForViewport);
    };
  }, [setIsPanelOpen]);

  useEffect(() => {
    let cancelled = false;

    authApi
      .getCurrentUser()
      .then((user) => {
        if (cancelled) return;
        setAuthUser(user);
        setPasswordRotation(user.password_rotation ?? null);
      })
      .catch(() => {
        if (!cancelled) {
          setPasswordRotation(null);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [setAuthUser]);

  useEffect(() => {
    if (passwordRotation?.required && pathname !== '/set-password') {
      router.replace('/set-password?rotation=1');
    }
  }, [passwordRotation?.required, pathname, router]);

  useEffect(() => {
    if (isMobileViewport()) {
      setIsPanelOpen(false);
    }
  }, [pathname, setIsPanelOpen]);

  useEffect(() => {
    const previousHtmlOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;

    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';

    return () => {
      document.documentElement.style.overflow = previousHtmlOverflow;
      document.body.style.overflow = previousBodyOverflow;
    };
  }, []);

  useEffect(() => {
    if (useExplicit) {
      setMeetingsLoading(false);
      return;
    }
    const projectId = activeProject?.id;
    if (!hasProjectStoreHydrated) {
      setMeetingsLoading(true);
      return;
    }
    if (!projectId) {
      setAutoMeetings([]);
      setMeetingsLoading(false);
      return;
    }
    let cancelled = false;
    setMeetingsLoading(true);
    MeetingsAPI.listMeetingsPaginated(projectId, {
      ordering: '-created_at',
      page: 1,
    })
      .then((res) => {
        if (cancelled) return;
        const { incoming } = splitMeetingRowsBySchedule(res.results);
        setAutoMeetings(incoming);
      })
      .catch(() => {
        if (!cancelled) setAutoMeetings([]);
      })
      .finally(() => {
        if (!cancelled) setMeetingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeProject?.id, hasProjectStoreHydrated, useExplicit]);

  const meetingsForPanel = useExplicit ? upcomingMeetings! : autoMeetings;
  const cachedElevatedUser = Boolean(
    authUser?.is_staff ||
    authUser?.is_org_admin ||
    authUser?.roles?.some((role) => role.toLowerCase().includes('admin') || role.toLowerCase().includes('owner')),
  );
  const bannerDays = passwordRotation?.days_until_expiry ?? passwordRotation?.warning_days ?? 7;
  const showPasswordRotationWarning =
    (passwordRotation?.warning && !passwordRotation.required) ||
    (!passwordRotation && cachedElevatedUser);
  const toggleMeetingsPanel = () => {
    if (isMobileViewport()) {
      setIsPanelOpen((prev) => !prev);
      return;
    }
    setIsPanelOpen((prev) => {
      const next = !prev;
      persistUpcomingMeetingsPanelPreference(next);
      return next;
    });
  };

  return (
    <NotificationDrawerProvider>
    <div className="flex h-screen w-full bg-[#F7F8FA] overflow-hidden">
      <DashboardSidebar />

      {/* Main content */}
      <div className="min-h-0 flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="flex items-center justify-between px-5 h-12 border-b border-gray-200 bg-white shrink-0">
          <div className="flex min-w-0 items-center gap-2 text-sm">
            {showBack && (
              <button
                type="button"
                onClick={handleBack}
                aria-label="Go back"
                title="Go back"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-900"
              >
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
            <span className="truncate text-gray-400">{breadcrumb.root}</span>
            <span className="text-gray-300">/</span>
            <span className="truncate font-medium text-gray-900">{breadcrumb.leaf}</span>
          </div>
          <div className="flex items-center gap-2">
            <NotificationBell alerts={alerts} />
            {!hideRightPanel && (
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleMeetingsPanel}
                className="h-7 px-2 text-xs text-gray-500 hover:text-gray-700 [&_svg]:mr-0 sm:[&_svg]:mr-1"
              >
                {isPanelOpen ? (
                  <><PanelRightClose className="w-4 h-4" /> <span className="hidden sm:inline">Hide Panel</span></>
                ) : (
                  <><PanelRightOpen className="w-4 h-4" /> <span className="hidden sm:inline">Show Panel</span></>
                )}
              </Button>
            )}
          </div>
        </header>
        {showPasswordRotationWarning && (
          <div className="flex items-center justify-between gap-3 border-b border-amber-200 bg-amber-50 px-5 py-2 text-sm text-amber-900">
            <div className="flex min-w-0 items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
              <span className="truncate">
                Your elevated account password expires in {bannerDays} {bannerDays === 1 ? 'day' : 'days'}.
              </span>
            </div>
            <button
              type="button"
              onClick={() => router.push('/set-password?rotation=1')}
              className="shrink-0 rounded-md border border-amber-300 bg-white px-3 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100"
            >
              Change password
            </button>
          </div>
        )}

        {/* Scrollable content */}
        <main className={`min-h-0 flex-1 overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable] p-3 space-y-4 sm:p-5 ${mainClassName}`}>
          {children}
        </main>
      </div>

      {!hideRightPanel && (
        <>
          {isPanelOpen && (
            <button
              type="button"
              aria-label="Close upcoming meetings overlay"
              className="fixed bottom-0 left-14 right-0 top-12 z-30 bg-gray-900/20 sm:hidden"
              onClick={() => setIsPanelOpen(false)}
            />
          )}
          <UpcomingMeetingsPanel
            meetings={meetingsForPanel}
            isOpen={isPanelOpen}
            loading={meetingsLoading}
            onClose={() => setIsPanelOpen(false)}
          />
        </>
      )}
      <AgentSidePanel />
      <QuickStartPostCreateChecklist />
    </div>

    {/* Notification context drawer — available on every page that uses DashboardLayout */}
    <NotificationDrawer />
    </NotificationDrawerProvider>
  );
}
