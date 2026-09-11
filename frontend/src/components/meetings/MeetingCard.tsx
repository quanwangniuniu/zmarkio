'use client';

import React from 'react';
import Link from 'next/link';
import { useBuildUrl } from '@/lib/buildUrl';
import { CalendarDays, FileText, ListChecks, Users } from 'lucide-react';
import type { MeetingListItem } from '@/types/meeting';
import MeetingStatusPill from './MeetingStatusPill';

const SNIPPET_SOURCE_LABEL: Record<string, string> = {
  transcript: 'Transcript',
  summary: 'Summary',
};

function highlightTitle(title: string, query?: string): React.ReactNode {
  if (!query?.trim()) return title;
  const regex = new RegExp(`(${query.trim()})`, 'gi');
  const parts = title.split(regex);
  return parts.map((part, i) =>
    regex.test(part)
      ? <mark key={i} className="rounded bg-yellow-100 px-0.5 font-semibold text-yellow-800">{part}</mark>
      : part
  );
}

interface Props {
  meeting: MeetingListItem;
  projectId: number | string;
  searchQuery?: string;
}

function formatScheduled(dateIso: string | null): string {
  if (!dateIso) return 'Unscheduled';
  const d = new Date(`${dateIso.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(d.getTime())) return 'Unscheduled';
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export default function MeetingCard({ meeting, projectId, searchQuery }: Props) {
  const buildUrl = useBuildUrl();
  const decisions =
    meeting.generated_decisions_count ?? meeting.decision_count ?? 0;
  const tasks = meeting.generated_tasks_count ?? meeting.task_count ?? 0;
  const participants = meeting.participants?.length ?? 0;

  return (
    <Link
      href={buildUrl(`/meetings/${meeting.slug}`)}
      className={`group block rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100 transition hover:-translate-y-0.5 hover:shadow-md hover:ring-gray-200 ${
        meeting.is_archived ? 'opacity-70' : ''
      }`}
      aria-label={`Open meeting ${meeting.title}`}
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="line-clamp-1 text-[15px] font-semibold text-gray-900">
          {highlightTitle(meeting.title || 'Untitled meeting', meeting.snippet_source === 'title' ? searchQuery : undefined)}
        </h3>
        <MeetingStatusPill status={meeting.status} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
        {meeting.meeting_type && (
          <span className="inline-flex items-center rounded-full bg-[#3CCED7]/10 px-2 py-0.5 text-[11px] font-medium text-[#0E8A96]">
            {meeting.meeting_type}
          </span>
        )}
        <span className="inline-flex items-center gap-1">
          <CalendarDays className="h-3 w-3" aria-hidden="true" />
          {formatScheduled(meeting.scheduled_date)}
        </span>
        <span className="inline-flex items-center gap-1">
          <Users className="h-3 w-3" aria-hidden="true" />
          {participants === 0 ? (
            <span className="italic text-gray-400">No participants</span>
          ) : (
            <span>{participants === 1 ? '1 participant' : `${participants} participants`}</span>
          )}
        </span>
        <span
          className={`inline-flex items-center gap-1 ${
            decisions > 0 ? 'text-gray-700' : ''
          }`}
        >
          <FileText className="h-3 w-3" aria-hidden="true" />
          {decisions} {decisions === 1 ? 'decision' : 'decisions'}
        </span>
        <span
          className={`inline-flex items-center gap-1 ${
            tasks > 0 ? 'text-gray-700' : ''
          }`}
        >
          <ListChecks className="h-3 w-3" aria-hidden="true" />
          {tasks} {tasks === 1 ? 'task' : 'tasks'}
        </span>
        {meeting.is_archived && (
          <span className="italic text-gray-400">· archived</span>
        )}
      </div>

      {searchQuery && meeting.search_snippet && meeting.snippet_source !== 'title' && (
        <div className="mt-2 border-t border-gray-50 pt-2">
          {meeting.snippet_source && SNIPPET_SOURCE_LABEL[meeting.snippet_source] && (
            <span className="mb-1 inline-block text-[10px] font-medium uppercase tracking-wide text-gray-400">
              {SNIPPET_SOURCE_LABEL[meeting.snippet_source]}
            </span>
          )}
          <p
            className="line-clamp-2 text-xs text-gray-500 [&_mark]:rounded [&_mark]:bg-yellow-100 [&_mark]:px-0.5 [&_mark]:font-semibold [&_mark]:text-yellow-800"
            // search_snippet contains only <mark> tags from PostgreSQL SearchHeadline — safe
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{ __html: meeting.search_snippet }}
          />
        </div>
      )}
    </Link>
  );
}
