'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useAuthStore } from '@/lib/authStore';
import { AgentAPI } from '@/lib/api/agentApi';

/** Shows actionable registry boot failures to staff and organisation admins. */
export function AgentRegistryStatusBanner() {
  const user = useAuthStore((state) => state.user);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!user || (!user.is_staff && !user.is_org_admin)) {
      setError(null);
      setUnavailable(false);
      return;
    }

    let active = true;
    let pending = false;
    const refresh = () => {
      if (pending) return;
      pending = true;
      AgentAPI.getConfigStatus()
      .then((status) => {
        if (!active) return;
        setUnavailable(false);
        setError(
          status.column_registry?.ok === false
            ? status.column_registry.error || 'Column registry is unavailable.'
            : null
        );
      })
      .catch(() => {
        if (active) setUnavailable(true);
      })
      .finally(() => { pending = false; });
    };
    refresh();
    const interval = window.setInterval(refresh, 30000);
    window.addEventListener('focus', refresh);

    return () => {
      active = false;
      window.clearInterval(interval);
      window.removeEventListener('focus', refresh);
    };
  }, [user]);

  if (!user || (!user.is_staff && !user.is_org_admin) || (!error && !unavailable)) return null;

  return (
    <div
      role="alert"
      className="flex shrink-0 items-start gap-2 border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">{unavailable ? 'Agent status is unavailable' : 'Agent startup is blocked'}</p>
        <p>{unavailable ? 'Cannot reach agent diagnostics. Check the backend logs and connection. Retrying automatically.' : error}</p>
        {!unavailable && <p className="mt-1">Fix the conflicting schema names or aliases. Restart the backend after fixing plugin registrations.</p>}
      </div>
    </div>
  );
}
