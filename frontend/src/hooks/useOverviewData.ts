'use client';

import { useEffect, useState } from 'react';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import { DecisionAPI } from '@/lib/api/decisionApi';
import { MeetingsAPI } from '@/lib/api/meetingsApi';
import { CampaignAPI } from '@/lib/api/campaignApi';
import { AssetAPI } from '@/lib/api/assetApi';
import { AlertingAPI } from '@/lib/api/alertingApi';
import type { AlertTask } from '@/lib/api/alertingApi';
import { ProjectAPI } from '@/lib/api/projectApi';
import type {
  OverviewMock,
  PendingDecisionDisplay,
  ActionItemDisplay,
  PendingAssetReviewItem,
  ActiveCampaignSummary,
  ProjectInvitationSummary,
} from '@/types/overview';
import type { DashboardSummary } from '@/types/dashboard';
import type { MeetingListItem } from '@/types/meeting';
import type { AlertData, AlertSeverity, AlertType } from '@/lib/mock/dashboardMock';
import { AdminAuditLogAPI } from '@/lib/api/auditLogApi';
import type { AdminAuditEvent } from '@/types/audit';

const EMPTY_SUMMARY: DashboardSummary = {
  time_metrics: {
    completed_last_7_days: 0,
    updated_last_7_days: 0,
    created_last_7_days: 0,
    due_soon: 0,
  },
  status_overview: { total_work_items: 0, breakdown: [] },
  priority_breakdown: [],
  types_of_work: [],
  recent_activity: [],
};

const EMPTY_DATA: OverviewMock = {
  taskSummary: EMPTY_SUMMARY,
  pendingDecisions: [],
  myDrafts: [],
  upcomingMeetings: [],
  actionItems: [],
  pendingAssetReviews: [],
  activeCampaigns: [],
  pendingInvitations: [],
  recentAuditEvents: [],
};

function mapAlertSeverity(sev: AlertTask['severity']): AlertSeverity {
  if (sev === 'critical' || sev === 'high') return 'critical';
  if (sev === 'medium') return 'warning';
  return 'info';
}

function mapAlertType(t: AlertTask['alert_type']): AlertType {
  if (
    t === 'spend_spike' ||
    t === 'performance_drop' ||
    t === 'delivery_issue' ||
    t === 'policy_violation'
  ) {
    return t;
  }
  return 'spend_spike';
}

function alertTitleFromType(t: AlertTask['alert_type']): string {
  switch (t) {
    case 'spend_spike':
      return 'Spend spike';
    case 'performance_drop':
      return 'Performance drop';
    case 'delivery_issue':
      return 'Delivery issue';
    case 'policy_violation':
      return 'Policy violation';
    default:
      return 'Alert';
  }
}

function scopeFromAffectedEntities(
  entities: AlertTask['affected_entities'],
): string {
  if (!Array.isArray(entities) || entities.length === 0) return '';
  const first = entities[0];
  if (!first || typeof first !== 'object') return '';
  const name = (first.name || first.label || first.id) as unknown;
  return typeof name === 'string' ? name : String(name ?? '');
}

function transformAlert(a: AlertTask): AlertData {
  const uiStatus: AlertData['status'] =
    a.status === 'open' || a.status === 'acknowledged' || a.status === 'in_progress'
      ? 'open'
      : 'accepted';
  return {
    id: a.id,
    type: mapAlertType(a.alert_type),
    severity: mapAlertSeverity(a.severity),
    title: alertTitleFromType(a.alert_type),
    scope: scopeFromAffectedEntities(a.affected_entities),
    why: a.investigation_notes || '',
    suggestion: a.resolution_steps || '',
    status: uiStatus,
    createdAt: a.created_at || new Date().toISOString(),
  };
}

function errMessage(reason: unknown): string {
  if (reason && typeof reason === 'object' && 'message' in reason) {
    const msg = (reason as { message?: unknown }).message;
    if (typeof msg === 'string') return msg;
  }
  return 'Failed';
}

export interface UseOverviewDataReturn {
  data: OverviewMock;
  alerts: AlertData[];
  loading: boolean;
  errors: Record<string, string>;
}

export function useOverviewData(projectId: number | string | null | undefined): UseOverviewDataReturn {
  const [data, setData] = useState<OverviewMock>(EMPTY_DATA);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [loadedProjectId, setLoadedProjectId] = useState<number | string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!projectId) {
      setData(EMPTY_DATA);
      setAlerts([]);
      setErrors({});
      setLoadedProjectId(null);
      return;
    }

    let cancelled = false;
    const pid = projectId;
    const pidStr = String(pid);

    const run = async () => {
      const nextErrors: Record<string, string> = {};

      const initial = await Promise.allSettled([
        DashboardAPI.getSummary({ project_id: pid }),
        DecisionAPI.listDecisions(pid, { status: 'AWAITING_APPROVAL' }),
        DecisionAPI.listDecisions(pid, { status: 'DRAFT' }),
        MeetingsAPI.listMeetingsPaginated(pid, { ordering: 'scheduled_date', page: 1 }),
        CampaignAPI.getCampaigns({ project: pidStr, status: 'active' }),
        ProjectAPI.getMyPendingInvitations(pid),
        AssetAPI.getAssets(),
        AlertingAPI.listAlertTasks({ status: 'open' }),
        AdminAuditLogAPI.list({ project_id: pid }),
      ]);

      if (cancelled) return;

      let taskSummary: DashboardSummary = EMPTY_SUMMARY;
      const rSummary = initial[0];
      if (rSummary.status === 'fulfilled') {
        taskSummary = rSummary.value.data;
      } else {
        nextErrors.taskSummary = errMessage(rSummary.reason);
      }

      let pendingDecisions: PendingDecisionDisplay[] = [];
      const rPending = initial[1];
      if (rPending.status === 'fulfilled') {
        pendingDecisions = rPending.value.items.map((d) => ({
          ...d,
          riskLevel: d.riskLevel ?? null,
        }));
      } else {
        nextErrors.pendingDecisions = errMessage(rPending.reason);
      }

      let myDrafts: PendingDecisionDisplay[] = [];
      const rDrafts = initial[2];
      if (rDrafts.status === 'fulfilled') {
        myDrafts = rDrafts.value.items.map((d) => ({
          ...d,
          riskLevel: d.riskLevel ?? null,
        }));
      } else {
        nextErrors.myDrafts = errMessage(rDrafts.reason);
      }

      let upcomingMeetings: MeetingListItem[] = [];
      const rMeetings = initial[3];
      if (rMeetings.status === 'fulfilled') {
        const today = new Date().toISOString().slice(0, 10);
        upcomingMeetings = rMeetings.value.results
          .filter((m) => !m.is_archived && (!m.scheduled_date || m.scheduled_date >= today))
          .slice(0, 5);
      } else {
        nextErrors.upcomingMeetings = errMessage(rMeetings.reason);
      }

      let activeCampaigns: ActiveCampaignSummary[] = [];
      const rCampaigns = initial[4];
      if (rCampaigns.status === 'fulfilled') {
        const raw = rCampaigns.value.data as unknown;
        const list: unknown[] = Array.isArray(raw)
          ? raw
          : raw && typeof raw === 'object' && Array.isArray((raw as { results?: unknown[] }).results)
            ? ((raw as { results: unknown[] }).results)
            : [];
        if (list.length > 0) {
          activeCampaigns = [{ status: 'active', count: list.length }];
        }
      } else {
        nextErrors.activeCampaigns = errMessage(rCampaigns.reason);
      }

      let pendingInvitations: ProjectInvitationSummary[] = [];
      const rInvites = initial[5];
      if (rInvites.status === 'fulfilled') {
        pendingInvitations = rInvites.value.map((inv) => ({
          id: inv.id,
          projectName: inv.project?.name || '',
          invitedBy:
            inv.invited_by?.name ||
            inv.invited_by?.username ||
            inv.invited_by?.email ||
            '',
          createdAt: inv.created_at || '',
        }));
      } else {
        nextErrors.pendingInvitations = errMessage(rInvites.reason);
      }

      let pendingAssetReviews: PendingAssetReviewItem[] = [];
      const rAssets = initial[6];
      if (rAssets.status === 'fulfilled') {
        const items = rAssets.value.results || [];
        pendingAssetReviews = items
          .filter((a) => a.status === 'PendingReview' || a.status === 'UnderReview')
          .slice(0, 10)
          .map((a) => ({
            id: a.id,
            name: a.tags?.[0] || `Asset #${a.id}`,
            ownerName: `User ${a.owner}`,
            submittedAt: a.updated_at || a.created_at,
            assetType: a.tags?.join(', ') || 'asset',
          }));
      } else {
        nextErrors.pendingAssetReviews = errMessage(rAssets.reason);
      }

      let alertsOut: AlertData[] = [];
      const rAlerts = initial[7];
      if (rAlerts.status === 'fulfilled') {
        const raw = rAlerts.value.data as unknown;
        const list: AlertTask[] = Array.isArray(raw)
          ? (raw as AlertTask[])
          : raw && typeof raw === 'object' && Array.isArray((raw as { results?: unknown[] }).results)
            ? ((raw as { results: AlertTask[] }).results)
            : [];
        alertsOut = list.map(transformAlert);
      } else {
        nextErrors.alerts = errMessage(rAlerts.reason);
      }

      let recentAuditEvents: AdminAuditEvent[] = [];
      const rAudit = initial[8];
      if (rAudit.status == 'fulfilled'){
        const raw = rAudit.value.data as { results?: AdminAuditEvent[] } | AdminAuditEvent[];
        recentAuditEvents = Array.isArray(raw) ? raw : (raw.results ?? []);
      } else{
        nextErrors.recentAuditEvents = errMessage(rAudit.reason);
      }


      let actionItems: ActionItemDisplay[] = [];
      if (upcomingMeetings.length > 0) {
        const pool = upcomingMeetings.slice(0, 5);
        const aiResults = await Promise.allSettled(
          pool.map((m) =>
            MeetingsAPI.listMeetingActionItems(pid, m.slug ?? m.id).then((items) => ({
              meetingId: m.id,
              meetingTitle: m.title,
              items,
            })),
          ),
        );
        if (cancelled) return;
        const aggregated: ActionItemDisplay[] = [];
        aiResults.forEach((r) => {
          if (r.status === 'fulfilled') {
            r.value.items
              .filter((it) => !it.is_resolved && it.converted_task_id == null)
              .forEach((it) => {
                aggregated.push({
                  ...it,
                  meeting_title: r.value.meetingTitle,
                  due_date: null,
                });
              });
          }
        });
        if (aggregated.length === 0 && aiResults.some((r) => r.status === 'rejected')) {
          nextErrors.actionItems = 'Some meetings failed to load action items';
        }
        actionItems = aggregated.slice(0, 10);
      }

      if (cancelled) return;

      setData({
        taskSummary,
        pendingDecisions,
        myDrafts,
        upcomingMeetings,
        actionItems,
        pendingAssetReviews,
        activeCampaigns,
        pendingInvitations,
        recentAuditEvents,
      });
      setAlerts(alertsOut);
      setErrors(nextErrors);
      setLoadedProjectId(pid);
    };

    run().catch((e) => {
      if (cancelled) return;
      setErrors({ fatal: errMessage(e) });
      setLoadedProjectId(pid);
    });

    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const loading = projectId != null && loadedProjectId !== projectId;

  return { data, alerts, loading, errors };
}

export default useOverviewData;
