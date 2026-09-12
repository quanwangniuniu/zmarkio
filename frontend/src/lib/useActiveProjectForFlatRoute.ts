'use client';

import { useProjectStore } from '@/lib/projectStore';
import { useStripProjectIdFromUrl } from '@/lib/useStripProjectIdFromUrl';

/**
 * Flat list routes (`/tasks`, `/decisions`, …) resolve the active project from the
 * Zustand store only — never from `?project_id=`.
 */
export function useActiveProjectForFlatRoute() {
  useStripProjectIdFromUrl();
  const activeProject = useProjectStore((s) => s.activeProject);
  const hasProjectStoreHydrated = useProjectStore((s) => s.hasHydrated);

  const projectId = activeProject?.id ?? activeProject?.slug ?? null;

  return {
    projectId,
    activeProject,
    projectContextLoading: !hasProjectStoreHydrated,
  };
}
