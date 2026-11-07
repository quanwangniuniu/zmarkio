'use client';

import { useEffect, useState, type ReactNode } from 'react';

const MSW_ENABLED =
  process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_ENABLE_MSW === 'true';

let workerStart: Promise<unknown> | null = null;

export function MswProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(!MSW_ENABLED);

  useEffect(() => {
    if (!MSW_ENABLED) return;
    workerStart ??= Promise.all([
      import('msw/browser'),
      import('@/lib/orgCustomization/mocks/handlers'),
    ])
      .then(([{ setupWorker }, { orgCustomizationHandlers }]) =>
        setupWorker(...orgCustomizationHandlers).start({
          serviceWorker: { url: '/msw/mockServiceWorker.js', options: { scope: '/' } },
          onUnhandledRequest: 'bypass',
          quiet: true,
        }),
      );
    workerStart.finally(() => setReady(true));
  }, []);

  return ready ? <>{children}</> : null;
}
