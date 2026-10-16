'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import {
  resolveOverviewSectionId,
  scrollOverviewMainToSectionWithRetry,
} from '@/lib/overviewScroll';

/**
 * Scrolls to a section on Overview when the URL hash is set (e.g. /overview#project-team).
 */
export function useOverviewSectionHashScroll(ready: boolean) {
  const pathname = usePathname();

  useEffect(() => {
    if (!ready || (pathname !== '/overview' && !pathname?.endsWith('/overview'))) return;

    const scrollToSection = () => {
      const sectionId = resolveOverviewSectionId();
      if (!sectionId) return;
      scrollOverviewMainToSectionWithRetry(sectionId);
    };

    scrollToSection();
    window.addEventListener('hashchange', scrollToSection);
    return () => window.removeEventListener('hashchange', scrollToSection);
  }, [ready, pathname]);
}
