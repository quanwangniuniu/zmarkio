'use client';

import { createContext, useContext } from 'react';
import type { ProjectData } from '@/lib/api/projectApi';

/** The project resolved from the URL, independent of persisted picker state. */
export const ProjectRouteContext = createContext<ProjectData | null>(null);

export function useProjectRoute() {
  return useContext(ProjectRouteContext);
}
