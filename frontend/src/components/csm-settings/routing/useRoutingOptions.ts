'use client';

import { useEffect, useState } from 'react';
import CsmAPI from '@/lib/api/csmApi';
import { ExperienceGroupAPI } from '@/lib/api/experienceGroupApi';
import { OrganisationAPI } from '@/lib/api/organisationAPI';
import { RoutingRuleAPI } from '@/lib/api/routingRuleApi';
import { SupportChannelAPI } from '@/lib/api/supportChannelApi';
import type { Queue } from '@/types/csm';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';
import type { RoutingVocabulary } from '@/types/routingRule';
import type { SupportChannelListItem } from '@/types/supportChannel';

export interface RoutingOptions {
  experienceGroups: ExperienceGroupListItem[];
  queues: Queue[];
  channels: SupportChannelListItem[];
  organisations: { id: number; name: string }[];
  vocabulary: RoutingVocabulary | null;
  loading: boolean;
  error: string | null;
}

const EMPTY: RoutingOptions = {
  experienceGroups: [],
  queues: [],
  channels: [],
  organisations: [],
  vocabulary: null,
  loading: true,
  error: null,
};

/** Lookup data shared by the routing rules page and the sandbox. */
export function useRoutingOptions(projectId: number, enabled: boolean): RoutingOptions {
  const [state, setState] = useState<RoutingOptions>(EMPTY);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true, error: null }));
    Promise.all([
      ExperienceGroupAPI.list({ project: projectId }),
      CsmAPI.listProjectQueues(projectId),
      SupportChannelAPI.list(projectId, { includeInactive: true }),
      OrganisationAPI.myAdminOrgs(),
      RoutingRuleAPI.vocabulary(projectId),
    ])
      .then(([egRes, queues, channels, orgRes, vocabulary]) => {
        if (cancelled) return;
        const experienceGroups = Array.isArray(egRes.data) ? egRes.data : egRes.data.results ?? [];
        setState({
          experienceGroups,
          queues,
          channels,
          organisations: orgRes.data,
          vocabulary,
          loading: false,
          error: null,
        });
      })
      .catch(() => {
        if (!cancelled) {
          setState({ ...EMPTY, loading: false, error: 'Failed to load routing settings.' });
        }
      });
    return () => { cancelled = true; };
  }, [projectId, enabled]);

  return state;
}
