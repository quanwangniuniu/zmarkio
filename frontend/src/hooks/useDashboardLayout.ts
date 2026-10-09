'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import type { DashboardLayoutResponse, DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';

/** Load only the requested project's layout; discard responses from a previous mount. */
export function useDashboardLayout(projectId: number | string) {
  const [layout, setLayout] = useState<DashboardLayoutResponse | null>(null);
  const [error, setError] = useState(false);
  const versionRef = useRef(0);
  const load = useCallback(async () => {
    const version = ++versionRef.current;
    setError(false);
    setLayout(null);
    try {
      const { data } = await DashboardAPI.getLayout(projectId);
      if (version !== versionRef.current) return;
      if (String(projectId) !== String(data.project_id) && String(projectId) !== data.project_slug) {
        throw new Error('Dashboard layout belongs to a different project');
      }
      if (!Array.isArray(data.widgets) || !data.configuration || !Array.isArray(data.configuration.widgets)) {
        throw new Error('Invalid dashboard layout response');
      }
      setLayout(data);
    } catch {
      if (version === versionRef.current) setError(true);
    }
  }, [projectId]);
  useEffect(() => {
    void load();
    return () => { versionRef.current += 1; };
  }, [load]);
  return { layout, error, retry: load };
}

/** Coalesce optimistic edits and save the newest layout without overlapping requests. */
export function useDashboardLayoutSave(projectId: number | string, initialWidgets: Widget[]) {
  const [widgets, setWidgets] = useState<Widget[]>(initialWidgets);
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'failed'>('saved');
  const widgetsRef = useRef(initialWidgets);
  const pendingRef = useRef<Widget[] | null>(null);
  const savingRef = useRef(false);
  const flush = useCallback(async () => {
    if (savingRef.current || !pendingRef.current) return;
    savingRef.current = true;
    setSaveState('saving');
    try {
      while (pendingRef.current) {
        const submitted: Widget[] = pendingRef.current;
        await DashboardAPI.saveLayout(projectId, submitted);
        if (pendingRef.current === submitted) pendingRef.current = null;
      }
      setSaveState('saved');
    } catch {
      // Keep the newest local layout for a manual retry or the next gesture.
      setSaveState('failed');
    } finally {
      savingRef.current = false;
    }
  }, [projectId]);

  const update = (change: (current: Widget[]) => Widget[]) => {
    const next = change(widgetsRef.current);
    if (JSON.stringify(next) === JSON.stringify(widgetsRef.current)) return;
    widgetsRef.current = next;
    setWidgets(next);
    pendingRef.current = next;
    void flush();
  };

  return { widgets, widgetsRef, saveState, update, flush };
}
