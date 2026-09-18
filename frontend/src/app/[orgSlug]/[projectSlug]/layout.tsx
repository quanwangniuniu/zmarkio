'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { ProjectAPI } from '@/lib/api/projectApi';
import { OrganizationAPI } from '@/lib/api/organizationApi';
import { useProjectStore } from '@/lib/projectStore';
import { useAuthStore } from '@/lib/authStore';
import { DashboardPanelPreferenceProvider } from '@/components/dashboard/DashboardPanelPreferenceContext';
import { readUpcomingMeetingsPanelOpen } from '@/lib/dashboardPanelPreferences';


/**
 * Resolves [orgSlug]/[projectSlug] from the URL and syncs org + project
 * context.
 *
 * Multi-tenant note: the backend's tenant_schema middleware sets PostgreSQL's
 * search_path from request.user.current_organization_id on every request. If
 * the URL's org doesn't match the user's current organization, the backend
 * will keep serving data scoped to the wrong tenant — so we can't just
 * validate the org slug here, we have to actually call
 * OrganizationAPI.switchOrganization() when they diverge, mirroring the
 * switch flow in organizations/[orgId]/page.tsx (lines 176-189).
 */
export default function OrgProjectLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ orgSlug: string; projectSlug: string }>();
  const setActiveProject = useProjectStore((s) => s.setActiveProject);
  const user = useAuthStore((s) => s.user);
  const authHasHydrated = useAuthStore((s) => s.hasHydrated);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const orgSlug = params?.orgSlug;
    const projectSlug = params?.projectSlug;
    if (!orgSlug || !projectSlug) {
      setError('Missing org or project in URL.');
      setLoading(false);
      return;
    }

    // Wait for the persisted auth store to rehydrate before comparing
    // user.current_organization — otherwise this effect can run against a
    // stale/empty user on a fresh page load and fire a needless switch call.
    if (!authHasHydrated) {
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const org = await OrganizationAPI.getOrganizationDetail(orgSlug);
        if (cancelled) return;

        if (user?.current_organization?.id !== org.id) {
          await OrganizationAPI.switchOrganization(org.id);
          if (cancelled) return;

          // Keep the auth store's current_organization in sync so later
          // navigations within the same org don't see a stale value here and
          // re-trigger a redundant switch call.
          const currentUser = useAuthStore.getState().user;
          if (currentUser) {
            useAuthStore.getState().setUser({
              ...currentUser,
              current_organization: { id: org.id, name: org.name, slug: org.slug },
            });
          }

          useProjectStore.getState().clearProjects();
        }

        const project = await ProjectAPI.getProject(projectSlug);
        if (cancelled) return;
        setActiveProject(project);
      } catch (err: any) {
        if (cancelled) return;
        const message =
          err?.response?.data?.error || err?.response?.data?.detail || 'Failed to load organization/project context.';
        setError(message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [params?.orgSlug, params?.projectSlug, user?.current_organization?.id, authHasHydrated, setActiveProject]);

  if (error) {
    return <div className="p-6 text-sm text-rose-600">{error}</div>;
  }

  if (loading) {
    return null;
  }

  // return <>{children}</>;

  return (
    <DashboardPanelPreferenceProvider
      initialUpcomingMeetingsPanelOpen={readUpcomingMeetingsPanelOpen()}
    >
    {children}
    </DashboardPanelPreferenceProvider>
  )

}
