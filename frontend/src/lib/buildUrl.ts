import { useMemo } from 'react';
import { useAuthStore } from '@/lib/authStore';
import { useProjectStore } from '@/lib/projectStore';

/**
 * Prefixes app paths with /[orgSlug]/[projectSlug].
 *
 * Falls back to the original, unprefixed path when either slug is
 * unavailable, so callers keep working exactly as before (flat routes)
 * until both org and project context are known.
 */
function withOrgProjectPrefix(
  path: string,
  orgSlug: string | null | undefined,
  projectSlug: string | null | undefined
): string {
  if (!orgSlug || !projectSlug) {
    return path;
  }

  const prefix = `/${orgSlug}/${projectSlug}`;

  // Already prefixed (e.g. path was built with buildUrl twice, or a caller
  // passed in an already-nested path) — don't double it up.
  if (path === prefix || path.startsWith(`${prefix}/`) || path.startsWith(`${prefix}?`)) {
    return path;
  }

  // Public booking pages live at /book/<org>/<slug>, outside the app shell.
  // Prefixing them produces /{org}/{project}/book/... which 404s.
  const bare = path.startsWith('/') ? path : `/${path}`;
  if (bare === '/book' || bare.startsWith('/book/') || bare.startsWith('/book?')) {
    return bare;
  }

  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${prefix}${suffix}`;
}

/**
 * Non-reactive version for use outside React components — event handlers,
 * plain utility functions, anywhere a hook can't be called. Reads store
 * state directly via getState(), so it reflects whatever org/project is
 * current at call time but won't re-run on its own if they change.
 */
export function buildUrl(path: string): string {
  const orgSlug = useAuthStore.getState().user?.current_organization?.slug ?? null;
  const projectSlug = useProjectStore.getState().activeProject?.slug ?? null;
  return withOrgProjectPrefix(path, orgSlug, projectSlug);
}

/**
 * Reactive hook version for use inside React components — subscribes to
 * org/project slug changes so the returned function (and anything that
 * depends on it) updates automatically when either changes.
 */
export function useBuildUrl(): (path: string) => string {
  const orgSlug = useAuthStore((s) => s.user?.current_organization?.slug ?? null);
  const projectSlug = useProjectStore((s) => s.activeProject?.slug ?? null);

  return useMemo(
    () => (path: string) => withOrgProjectPrefix(path, orgSlug, projectSlug),
    [orgSlug, projectSlug]
  );
}
