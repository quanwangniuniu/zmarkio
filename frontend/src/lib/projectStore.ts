'use client';

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { ProjectData } from './api/projectApi';

interface ProjectState {
  projects: ProjectData[];
  activeProject: ProjectData | null;
  activeProjectIds: (number | string)[];
  inactiveProjectIds: (number | string)[];
  completedProjectIds: (number | string)[];
  hasHydrated: boolean;
  loading: boolean;
  error: string | null;
  setProjects: (projects: ProjectData[]) => void;
  setActiveProject: (project: ProjectData | null) => void;
  setActiveProjectIds: (ids: (number | string)[] | ((prev: (number | string)[]) => (number | string)[])) => void;
  toggleActiveProjectId: (id: number | string) => void;
  setInactiveProjectIds: (ids: (number | string)[] | ((prev: (number | string)[]) => (number | string)[])) => void;
  addInactiveProjectId: (id: number | string) => void;
  toggleCompletedProjectId: (id: number | string) => void;
  setCompletedProjectIds: (ids: (number | string)[] | ((prev: (number | string)[]) => (number | string)[])) => void;
  setHasHydrated: (hasHydrated: boolean) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  clearProjects: () => void;
}

const ACTIVE_PROJECT_COOKIE_NAME = 'active-project';
const PROJECT_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;
const writeActiveProjectCookie = (project: ProjectData | null) => {
  if (typeof document === 'undefined') return;

  if (!project) {
    document.cookie = `${encodeURIComponent(ACTIVE_PROJECT_COOKIE_NAME)}=; Max-Age=0; Path=/; SameSite=Lax`;
    return;
  }

  document.cookie = `${encodeURIComponent(ACTIVE_PROJECT_COOKIE_NAME)}=${encodeURIComponent(
    JSON.stringify({
      id: project.id,
      slug: project.slug,
      name: project.name,
      organization: project.organization,
      total_monthly_budget: project.total_monthly_budget,
      is_active: project.is_active,
    })
  )}; Max-Age=${PROJECT_COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Lax`;
};


export const useProjectStore = create<ProjectState>()(
  persist(
    (set) => ({
      projects: [],
      activeProject: null,
      activeProjectIds: [],
      inactiveProjectIds: [],
      completedProjectIds: [],
      hasHydrated: false,
      loading: false,
      error: null,
      setProjects: (projects) => set({ projects }),
      setActiveProject: (activeProject) => {
        writeActiveProjectCookie(activeProject);
        set((state) => ({
          activeProject,
          activeProjectIds: activeProject?.id
            ? Array.from(new Set([...state.activeProjectIds, activeProject.id]))
            : state.activeProjectIds,
          inactiveProjectIds: activeProject?.id
            ? state.inactiveProjectIds.filter((id) => id !== activeProject.id)
            : state.inactiveProjectIds,
        }));
      },
      setActiveProjectIds: (ids) =>
        set((state) => {
          const resolvedIds = typeof ids === 'function' ? ids(state.activeProjectIds) : ids;
          const uniqueIds = Array.from(new Set(resolvedIds));
          return {
            activeProjectIds: uniqueIds,
            inactiveProjectIds: state.inactiveProjectIds.filter((id) => !uniqueIds.includes(id)),
          };
        }),
      toggleActiveProjectId: (id) =>
        set((state) => {
          const next = new Set(state.activeProjectIds);
          if (next.has(id)) {
            next.delete(id);
          } else {
            next.add(id);
          }
          return {
            activeProjectIds: Array.from(next),
            inactiveProjectIds: state.inactiveProjectIds.filter((item) => item !== id),
          };
        }),
      setInactiveProjectIds: (ids) =>
        set((state) => {
          const resolvedIds = typeof ids === 'function' ? ids(state.inactiveProjectIds) : ids;
          return { inactiveProjectIds: Array.from(new Set(resolvedIds)) };
        }),
      addInactiveProjectId: (id) =>
        set((state) => ({
          inactiveProjectIds: Array.from(new Set([...state.inactiveProjectIds, id])),
          activeProjectIds: state.activeProjectIds.filter((item) => item !== id),
        })),
      toggleCompletedProjectId: (id) =>
        set((state) => {
          const next = new Set(state.completedProjectIds);
          if (next.has(id)) {
            next.delete(id);
          } else {
            next.add(id);
          }
          return { completedProjectIds: Array.from(next) };
        }),
      setCompletedProjectIds: (ids) =>
        set((state) => {
          const resolvedIds = typeof ids === 'function' ? ids(state.completedProjectIds) : ids;
          return { completedProjectIds: Array.from(new Set(resolvedIds)) };
        }),
      setHasHydrated: (hasHydrated) => set({ hasHydrated }),
      setLoading: (loading) => set({ loading }),
      setError: (error) => set({ error }),
      clearProjects: () => {
        writeActiveProjectCookie(null);
        set({
          projects: [],
          activeProject: null,
          activeProjectIds: [],
          inactiveProjectIds: [],
          completedProjectIds: [],
          hasHydrated: true,
          loading: false,
          error: null,
        });
      },
    }),
    {
      name: 'project-storage-v1',
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
        // One-time migration from old key. Idempotent: skipped if new key already
        // exists. Old key is left in place; follow-up cleanup ticket removes it.
        if (typeof window === 'undefined') return;
        // Guard: skip if new key already has real project data.
        try {
          const newRaw = window.localStorage.getItem('project-storage-v1');
          if (newRaw) {
            const newParsed = JSON.parse(newRaw);
            const s = newParsed?.state;
            if (
              s?.activeProject !== undefined ||
              (s?.activeProjectIds?.length ?? 0) > 0 ||
              (s?.completedProjectIds?.length ?? 0) > 0 ||
              (s?.inactiveProjectIds?.length ?? 0) > 0
            ) return;
          }
        } catch { /* malformed new key — fall through to migration */ }
        try {
          const raw = window.localStorage.getItem('project-storage');
          if (!raw) return;
          const parsed = JSON.parse(raw);
          const old = parsed?.state;
          if (!old) return;
          useProjectStore.setState({
            activeProject: old.activeProject ?? null,
            activeProjectIds: old.activeProjectIds ?? [],
            inactiveProjectIds: old.inactiveProjectIds ?? [],
            completedProjectIds: old.completedProjectIds ?? [],
          });
        } catch (e) {
          console.warn('[projectStore migration] Failed to migrate legacy persist key:', e);
        }
      },
      partialize: (state) => ({
        activeProject: state.activeProject,
        activeProjectIds: state.activeProjectIds,
        inactiveProjectIds: state.inactiveProjectIds,
        completedProjectIds: state.completedProjectIds,
      }),
    }
  )
);
