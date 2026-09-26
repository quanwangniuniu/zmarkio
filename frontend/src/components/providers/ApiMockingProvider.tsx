'use client';

/**
 * Mock API switch: when NEXT_PUBLIC_API_MOCKING=on, starts the MSW service
 * worker (public/msw/mockServiceWorker.js) with the registered handlers so
 * `npm run dev` serves mocks instead of the real backend. With the flag off
 * (the default) this renders children untouched and loads nothing.
 */
import { useEffect, type ReactNode } from 'react';

// Module-level singleton: React StrictMode mounts effects twice in dev, and
// stopping one worker instance unregisters the service-worker registration
// shared with the other. Start once per page session; never stop on cleanup.
let startPromise: Promise<void> | null = null;

function startMockApiOnce(): Promise<void> {
  if (!startPromise) {
    startPromise = (async () => {
      const { setupWorker } = await import('msw/browser');
      const { orgCustomizationHandlers } = await import(
        '../../../public/msw/orgCustomization.handlers'
      );
      const worker = setupWorker(...orgCustomizationHandlers);
      await worker.start({
        serviceWorker: {
          url: '/msw/mockServiceWorker.js',
          options: { scope: '/' },
        },
        onUnhandledRequest: 'bypass',
      });
      console.info(
        `[ApiMocking] enabled with ${orgCustomizationHandlers.length} handler(s)`
      );
    })();
  }
  return startPromise;
}

export function ApiMockingProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_API_MOCKING !== 'on') return;
    void startMockApiOnce();
  }, []);

  return <>{children}</>;
}
