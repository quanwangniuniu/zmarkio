'use client';

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { RoutingRuleAPI } from '@/lib/api/routingRuleApi';
import type { RoutingRule } from '@/types/routingRule';

/** Rules for one experience group, with optimistic toggle/reorder. */
export function useRoutingRules(projectId: number, experienceGroupId: number | null) {
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (experienceGroupId === null) {
      setRules([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setRules(await RoutingRuleAPI.list(projectId, experienceGroupId));
    } catch {
      setError('Failed to load routing rules.');
    } finally {
      setLoading(false);
    }
  }, [projectId, experienceGroupId]);

  useEffect(() => { load(); }, [load]);

  const upsert = useCallback((saved: RoutingRule) => {
    setRules((prev) => {
      const index = prev.findIndex((r) => r.id === saved.id);
      if (index < 0) return [...prev, saved];
      const next = [...prev];
      next[index] = saved;
      return next;
    });
  }, []);

  const reorder = useCallback(async (next: RoutingRule[]) => {
    if (experienceGroupId === null) return;
    const previous = rules;
    setRules(next);
    try {
      setRules(await RoutingRuleAPI.reorder(projectId, experienceGroupId, next.map((r) => r.id)));
      toast.success('Rule order saved.');
    } catch {
      setRules(previous);
      toast.error('Could not save rule order.');
    }
  }, [projectId, experienceGroupId, rules]);

  const toggle = useCallback(async (rule: RoutingRule) => {
    upsert({ ...rule, is_enabled: !rule.is_enabled });
    try {
      upsert(await RoutingRuleAPI.update(rule.id, { is_enabled: !rule.is_enabled }));
    } catch {
      upsert(rule);
      toast.error('Could not update rule.');
    }
  }, [upsert]);

  const remove = useCallback(async (rule: RoutingRule) => {
    try {
      await RoutingRuleAPI.remove(rule.id);
      setRules((prev) => prev.filter((r) => r.id !== rule.id));
      toast.success('Rule deleted.');
    } catch {
      toast.error('Could not delete rule.');
    }
  }, []);

  return { rules, loading, error, load, upsert, reorder, toggle, remove };
}
