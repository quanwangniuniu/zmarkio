'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import ChatFAB from '@/components/global-chat/ChatFAB';
import { useActiveProjectForFlatRoute } from '@/lib/useActiveProjectForFlatRoute';
import { MeetingsAPI } from '@/lib/api/meetingsApi';
import type {
  MeetingListItem,
  MeetingListQueryParams,
  PaginatedMeetingsList,
} from '@/types/meeting';
import {
  DEFAULT_MEETING_SORT,
  type MeetingSortKey,
} from '@/lib/meetings/meetingSectionSort';
import { splitMeetingRowsBySchedule } from '@/lib/meetings/meetingScheduleSplit';
import MeetingsHeader from '@/components/meetings/MeetingsHeader';
import MeetingsFilterBar from '@/components/meetings/MeetingsFilterBar';
import MeetingsHubColumns from '@/components/meetings/MeetingsHubColumns';
import CreateMeetingDialog from '@/components/meetings/CreateMeetingDialog';
import {
  EMPTY_ADVANCED_FILTER,
  advancedFilterToParams,
  type AdvancedFilterState,
} from '@/components/meetings/AdvancedFilterDialog';

const DEFAULT_TYPE_FALLBACKS = [
  'Planning',
  'Client Meeting',
  'Stand-up',
  'Review & Retrospective',
  'Deployment Sync',
];

function dedupeByKey<T>(rows: T[], key: (row: T) => string): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const r of rows) {
    const k = key(r);
    if (!k || seen.has(k)) continue;
    seen.add(k);
    out.push(r);
  }
  return out;
}

export default function MeetingsV2Page() {
  const { projectId, activeProject } = useActiveProjectForFlatRoute();

  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [typeSlug, setTypeSlug] = useState('');
  const [tagSlug, setTagSlug] = useState('');
  const [advanced, setAdvanced] = useState<AdvancedFilterState>(EMPTY_ADVANCED_FILTER);
  const [incomingSort, setIncomingSort] = useState<MeetingSortKey>(DEFAULT_MEETING_SORT);
  const [completedSort, setCompletedSort] = useState<MeetingSortKey>(DEFAULT_MEETING_SORT);
  const [createOpen, setCreateOpen] = useState(false);

  const [data, setData] = useState<PaginatedMeetingsList | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const searchTimerRef = useRef<number | null>(null);
  useEffect(() => {
    if (searchTimerRef.current) window.clearTimeout(searchTimerRef.current);
    searchTimerRef.current = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, 300);
    return () => {
      if (searchTimerRef.current) window.clearTimeout(searchTimerRef.current);
    };
  }, [search]);

  const queryParams = useMemo<MeetingListQueryParams>(() => {
    const base: MeetingListQueryParams = {
      ordering: '-created_at',
      page: 1,
    };
    if (debouncedSearch) base.q = debouncedSearch;
    if (typeSlug) base.meeting_type = [typeSlug];
    if (tagSlug) base.tag = tagSlug;
    const advParams = advancedFilterToParams(advanced);
    return { ...base, ...advParams };
  }, [debouncedSearch, typeSlug, tagSlug, advanced]);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setLoading(true);
    setErrorMessage(null);
    MeetingsAPI.listMeetingsPaginated(projectId, queryParams)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          (err as { response?: { data?: { detail?: string } }; message?: string })
            ?.response?.data?.detail ||
          (err as { message?: string })?.message ||
          'Could not load meetings.';
        setErrorMessage(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, queryParams, refreshToken]);

  const rows: MeetingListItem[] = data?.results ?? [];

  const { incoming, completed } = useMemo(
    () => splitMeetingRowsBySchedule(rows),
    [rows],
  );

  const typeOptions = useMemo(() => {
    const fromRows = rows
      .map((r) => ({ slug: r.meeting_type_slug, label: r.meeting_type }))
      .filter((t) => t.slug && t.label);
    return dedupeByKey(fromRows, (t) => t.slug).sort((a, b) =>
      a.label.localeCompare(b.label),
    );
  }, [rows]);

  const typeSuggestions = useMemo(() => {
    const labels = typeOptions.map((t) => t.label);
    const merged = [...new Set([...labels, ...DEFAULT_TYPE_FALLBACKS])];
    return merged.sort((a, b) => a.localeCompare(b));
  }, [typeOptions]);

  const tagOptions = useMemo(() => {
    const fromRows = rows.flatMap((r) => r.tags ?? []);
    return dedupeByKey(fromRows, (t) => t.slug).sort((a, b) =>
      a.label.localeCompare(b.label),
    );
  }, [rows]);

  const handleCreated = useCallback(() => {
    setRefreshToken((n) => n + 1);
  }, []);

  const incomingLaneTotal = data?.incomingLaneTotal ?? 0;
  const incomingResultCount = data?.incomingResultCount ?? 0;
  const completedLaneTotal = data?.completedLaneTotal ?? 0;
  const completedResultCount = data?.completedResultCount ?? 0;

  return (
    <DashboardLayout alerts={[]} upcomingMeetings={incoming}>
      <div className="mx-auto w-full max-w-7xl px-6 py-8">
        <MeetingsHeader
          projectName={activeProject?.name}
          onCreate={() => setCreateOpen(true)}
        />

        <div className="mt-5 space-y-5">
          <MeetingsFilterBar
            search={search}
            onSearchChange={setSearch}
            typeSlug={typeSlug}
            onTypeSlugChange={setTypeSlug}
            typeOptions={typeOptions}
            tagSlug={tagSlug}
            onTagSlugChange={setTagSlug}
            tagOptions={tagOptions}
            advanced={advanced}
            onAdvancedChange={setAdvanced}
          />

          {!projectId && (
            <div className="rounded-xl bg-white p-6 text-center text-sm text-gray-500 shadow-sm ring-1 ring-gray-100">
              Select a project to view meetings.
            </div>
          )}

          {projectId && errorMessage && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {errorMessage}
              <button
                type="button"
                onClick={() => setRefreshToken((n) => n + 1)}
                className="ml-3 text-xs font-medium text-rose-700 underline"
              >
                Retry
              </button>
            </div>
          )}

          {projectId && !errorMessage && (
            <MeetingsHubColumns
              incoming={incoming}
              completed={completed}
              incomingLaneTotal={incomingLaneTotal}
              incomingResultCount={incomingResultCount}
              completedLaneTotal={completedLaneTotal}
              completedResultCount={completedResultCount}
              incomingSort={incomingSort}
              onIncomingSortChange={setIncomingSort}
              completedSort={completedSort}
              onCompletedSortChange={setCompletedSort}
              projectId={projectId}
              onCreate={() => setCreateOpen(true)}
              searchQuery={debouncedSearch}
            />
          )}

          {projectId && loading && rows.length === 0 && !errorMessage && (
            <div className="rounded-xl bg-white p-6 text-center text-xs text-gray-400 shadow-sm ring-1 ring-gray-100">
              Loading meetings…
            </div>
          )}
        </div>

        {projectId && (
          <CreateMeetingDialog
            open={createOpen}
            onOpenChange={setCreateOpen}
            projectId={projectId}
            typeSuggestions={typeSuggestions}
            onCreated={() => {
              handleCreated();
              toast.dismiss();
            }}
          />
        )}
      </div>
      <ChatFAB />
    </DashboardLayout>
  );
}
