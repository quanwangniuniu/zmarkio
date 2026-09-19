'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useAuthStore } from '@/lib/authStore';
import { AgentAPI } from '@/lib/api/agentApi';

/** Shows actionable registry boot failures to staff and organisation admins. */
export function AgentRegistryStatusBanner() {
  const user = useAuthStore((state) => state.user);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user || (!user.is_staff && !user.is_org_admin)) {
      setError(null);
      return;
    }

    let active = true;
    AgentAPI.getConfigStatus()
      .then((status) => {
        if (!active) return;
        setError(
          status.column_registry?.ok
            ? null
            : status.column_registry?.error || 'Column registry is unavailable.'
        );
      })
      .catch(() => {
        // The stream error remains the source of truth if the diagnostics endpoint is unavailable.
      });

    return () => {
      active = false;
    };
  }, [user]);

  if (!error) return null;

  return (
    <div
      role="alert"
      className="flex shrink-0 items-start gap-2 border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">Agent startup is blocked</p>
        <p>{error}</p>
        <p className="mt-1">Fix the duplicate plugin column name, then reload the agent.</p>
      </div>
    </div>
  );
}
