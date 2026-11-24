'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import toast from 'react-hot-toast';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import ChatFAB from '@/components/global-chat/ChatFAB';
import { useActiveProjectForFlatRoute } from '@/lib/useActiveProjectForFlatRoute';
import { useBuildUrl } from '@/lib/buildUrl';
import api from '@/lib/api';
import { MeetingsAPI } from '@/lib/api/meetingsApi';
import type {
  Meeting,
  MeetingStatus,
  MeetingPartialUpdateRequest,
  AgendaItem,
  ParticipantLink,
  MeetingActionItem,
  ArtifactLink,
  ZoomPostMeeting,
} from '@/types/meeting';
import type { TaskData } from '@/types/task';
import MeetingDetailHeader from '@/components/meetings/detail/MeetingDetailHeader';
import OverviewSection from '@/components/meetings/detail/OverviewSection';
import SchedulePanel from '@/components/meetings/detail/SchedulePanel';
import ZoomPanel from '@/components/meetings/detail/ZoomPanel';
import ParticipantsPanel from '@/components/meetings/detail/ParticipantsPanel';
import AgendaSection from '@/components/meetings/detail/AgendaSection';
import ActionItemsSection from '@/components/meetings/detail/ActionItemsSection';
import ContextualKnowledgeSection from '@/components/meetings/detail/ContextualKnowledgeSection';
import RelatedArtifactsSection from '@/components/meetings/detail/RelatedArtifactsSection';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { isMeetingSurfaceEnabled } from '@/lib/meetings/meetingsConfig';

interface Member {
  id: number;
  user: { id: number; username?: string; email?: string; name?: string };
  role?: string;
  is_active?: boolean;
}

const DEFAULT_TYPE_FALLBACKS = [
  'Planning',
  'Client Meeting',
  'Stand-up',
  'Review & Retrospective',
  'Deployment Sync',
];

function getErrorMessage(err: unknown, fallback: string): string {
  const e = err as {
    response?: { data?: Record<string, unknown> };
    message?: string;
  };
  const d = e.response?.data || {};
  const pickStr = (v: unknown): string | undefined => {
    if (typeof v === 'string') return v;
    if (Array.isArray(v) && typeof v[0] === 'string') return v[0];
    return undefined;
  };
  return (
    pickStr(d.detail) ||
    pickStr(d.title) ||
    pickStr(d.meeting_type) ||
    pickStr(d.objective) ||
    pickStr(d.non_field_errors) ||
    e.message ||
    fallback
  );
}

export default function MeetingDetailPage() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const params = useParams<{ meetingId: string }>();
  const { projectId } = useActiveProjectForFlatRoute();
  const meetingId = String(params.meetingId);
  useOptionConfigVersion();

  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [availableTransitions, setAvailableTransitions] = useState<string[]>([]);
  const [agenda, setAgenda] = useState<AgendaItem[]>([]);
  const [participants, setParticipants] = useState<ParticipantLink[]>([]);
  const [actionItems, setActionItems] = useState<MeetingActionItem[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactLink[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [zoomConnected, setZoomConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creatingZoom, setCreatingZoom] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  const bumpRefresh = useCallback(() => setRefreshToken((n) => n + 1), []);

  const readOnly = useMemo(() => {
    if (!meeting) return true;
    return meeting.is_archived || meeting.status === 'archived';
  }, [meeting]);

  useEffect(() => {
    if (!projectId || !meetingId || Number.isNaN(meetingId)) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);

    const loadAll = async () => {
      try {
        const [meetingRes, lifecycleRes, agendaRes, participantsRes, actionItemsRes, artifactsRes, membersRes] =
          await Promise.all([
            MeetingsAPI.getMeeting(projectId, meetingId),
            MeetingsAPI.getLifecycle(projectId, meetingId),
            MeetingsAPI.listAgendaItems(projectId, meetingId),
            MeetingsAPI.listParticipants(projectId, meetingId),
            MeetingsAPI.listMeetingActionItems(projectId, meetingId),
            MeetingsAPI.listArtifacts(projectId, meetingId),
            api.get<any>(`/api/core/projects/${projectId}/members/`),
          ]);
        if (cancelled) return;
        setMeeting(meetingRes);
        setAvailableTransitions(lifecycleRes.available_transitions || []);
        setAgenda([...agendaRes].sort((a, b) => a.order_index - b.order_index));
        setParticipants(participantsRes);
        setActionItems(actionItemsRes);
        setArtifacts(artifactsRes);
        const memberData = membersRes.data as any;
        const rawMembers: Member[] = Array.isArray(memberData)
          ? memberData
          : memberData?.results ?? memberData?.items ?? [];
        setMembers(rawMembers);
      } catch (err) {
        if (cancelled) return;
        setLoadError(getErrorMessage(err, 'Could not load meeting.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadAll();
    return () => {
      cancelled = true;
    };
  }, [projectId, meetingId, refreshToken]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ connected?: boolean }>('/api/v1/zoom/status/')
      .then((res) => {
        if (!cancelled) setZoomConnected(Boolean(res.data?.connected));
      })
      .catch(() => {
        if (!cancelled) setZoomConnected(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const commitMeetingPatch = useCallback(
    async (patch: MeetingPartialUpdateRequest) => {
      if (!projectId || !meeting) return;
      try {
        const updated = await MeetingsAPI.patchMeeting(projectId, meeting.id, patch);
        setMeeting(updated);
        toast.success('Saved');
      } catch (err) {
        toast.error(getErrorMessage(err, 'Could not save.'));
        throw err;
      }
    },
    [projectId, meeting],
  );

  const handleTransitioned = useCallback(
    (nextStatus: MeetingStatus, nextAvailable: string[]) => {
      setMeeting((prev) => (prev ? { ...prev, status: nextStatus } : prev));
      setAvailableTransitions(nextAvailable);
    },
    [],
  );

  const handleDocumentOpen = useCallback(() => {
    if (!projectId || !meeting) return;
    router.push(buildUrl(`/meetings/${meeting.slug}/document`));
  }, [router, projectId, meeting]);

  const handleCreateZoomMeeting = useCallback(async () => {
    if (!meeting) return;
    setCreatingZoom(true);
    try {
      const topic = meeting.title || `Meeting #${meeting.id}`;
      const startIso = meeting.scheduled_date
        ? `${meeting.scheduled_date}T${(meeting.scheduled_time || '10:00:00').slice(0, 8)}`
        : new Date().toISOString();
      await api.post('/api/v1/zoom/meetings/', {
        topic,
        start_time: startIso,
        duration: 60,
      });
      toast.success('Zoom meeting created — paste the link into Schedule if needed.');
      bumpRefresh();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not create Zoom meeting.'));
    } finally {
      setCreatingZoom(false);
    }
  }, [meeting, bumpRefresh]);

  const typeSuggestions = useMemo(() => {
    const set = new Set<string>(DEFAULT_TYPE_FALLBACKS);
    if (meeting?.meeting_type) set.add(meeting.meeting_type);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [meeting]);

  const unresolvedActionItems = useMemo(
    () => actionItems.filter((it) => !it.is_resolved).length,
    [actionItems],
  );

  const zoomPostMeeting: ZoomPostMeeting | null = meeting?.zoom_post_meeting ?? null;

  return (
    <DashboardLayout alerts={[]} upcomingMeetings={[]}>
      <div className="mx-auto w-full max-w-7xl px-6 py-8">
        {!projectId && (
          <div className="rounded-xl bg-white p-6 text-center text-sm text-gray-500 shadow-sm ring-1 ring-gray-100">
            Select a project to view this meeting.
          </div>
        )}

        {projectId && loadError && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {loadError}
            <button
              type="button"
              onClick={bumpRefresh}
              className="ml-3 text-xs font-medium text-rose-700 underline"
            >
              Retry
            </button>
          </div>
        )}

        {projectId && !loadError && loading && !meeting && (
          <div className="rounded-xl bg-white p-6 text-center text-xs text-gray-400 shadow-sm ring-1 ring-gray-100">
            Loading meeting…
          </div>
        )}

        {projectId && meeting && (
          <div className="space-y-5">
            <MeetingDetailHeader
              projectId={projectId}
              meeting={meeting}
              availableTransitions={availableTransitions}
              participantsCount={participants.length}
              agendaCount={agenda.length}
              unresolvedActionItems={unresolvedActionItems}
              onTransitioned={handleTransitioned}
              onOpenDocument={handleDocumentOpen}
            />

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
              <div className="space-y-5">
                <OverviewSection
                  title={meeting.title}
                  meetingType={meeting.meeting_type}
                  objective={meeting.objective || ''}
                  readOnly={readOnly}
                  typeSuggestions={typeSuggestions}
                  onCommit={commitMeetingPatch}
                />

                <AgendaSection
                  projectId={projectId}
                  meetingId={meeting.id}
                  items={agenda}
                  readOnly={readOnly}
                  onItemsChange={setAgenda}
                />

                {isMeetingSurfaceEnabled('section.action_items') && (
                  <ActionItemsSection
                    projectId={projectId}
                    meetingId={meeting.id}
                    items={actionItems}
                    members={members}
                    readOnly={readOnly}
                    onItemsChange={setActionItems}
                    onTaskCreated={() => bumpRefresh()}
                  />
                )}

                {isMeetingSurfaceEnabled('panel.knowledge_nav') && (
                  <ContextualKnowledgeSection
                    projectId={projectId}
                    meetingId={meeting.id}
                    meetingTitle={meeting.title}
                    generatedDecisions={meeting.generated_decisions || []}
                    generatedTasks={meeting.generated_tasks || []}
                    readOnly={readOnly}
                    onMutated={bumpRefresh}
                  />
                )}

                <RelatedArtifactsSection
                  projectId={projectId}
                  meetingId={meeting.id}
                  relatedDecisions={meeting.related_decisions || []}
                  relatedTasks={meeting.related_tasks || []}
                  generatedDecisions={meeting.generated_decisions || []}
                  generatedTasks={meeting.generated_tasks || []}
                  artifacts={artifacts}
                  readOnly={readOnly}
                  onMutated={bumpRefresh}
                />
              </div>

              <aside className="space-y-5 lg:sticky lg:top-6 lg:self-start">
                <SchedulePanel
                  scheduledDate={meeting.scheduled_date}
                  scheduledTime={meeting.scheduled_time}
                  externalReference={meeting.external_reference}
                  readOnly={readOnly}
                  onCommit={commitMeetingPatch}
                />
                <ParticipantsPanel
                  projectId={projectId}
                  meetingId={meeting.id}
                  participants={participants}
                  members={members}
                  readOnly={readOnly}
                  onRefresh={bumpRefresh}
                />
                <ZoomPanel
                  zoomConnected={zoomConnected}
                  zoomPostMeeting={zoomPostMeeting}
                  onCreateZoomMeeting={handleCreateZoomMeeting}
                  creatingZoom={creatingZoom}
                />
              </aside>
            </div>
          </div>
        )}
      </div>
      <ChatFAB />
    </DashboardLayout>
  );
}
