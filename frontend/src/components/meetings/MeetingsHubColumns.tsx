'use client';

import type { MeetingListItem } from '@/types/meeting';
import type { MeetingSortKey } from '@/lib/meetings/meetingSectionSort';
import MeetingColumn from './MeetingColumn';

interface Props {
  incoming: MeetingListItem[];
  completed: MeetingListItem[];
  incomingLaneTotal: number;
  incomingResultCount: number;
  completedLaneTotal: number;
  completedResultCount: number;
  incomingSort: MeetingSortKey;
  onIncomingSortChange: (key: MeetingSortKey) => void;
  completedSort: MeetingSortKey;
  onCompletedSortChange: (key: MeetingSortKey) => void;
  projectId: number | string;
  onCreate: () => void;
  searchQuery?: string;
}

export default function MeetingsHubColumns({
  incoming,
  completed,
  incomingLaneTotal,
  incomingResultCount,
  completedLaneTotal,
  completedResultCount,
  incomingSort,
  onIncomingSortChange,
  completedSort,
  onCompletedSortChange,
  projectId,
  onCreate,
  searchQuery,
}: Props) {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2 lg:items-start">
      <MeetingColumn
        title="Incoming meetings"
        variant="incoming"
        meetings={incoming}
        resultCount={incomingResultCount}
        laneTotal={incomingLaneTotal}
        sortKey={incomingSort}
        onSortChange={onIncomingSortChange}
        projectId={projectId}
        onCreate={onCreate}
        searchQuery={searchQuery}
      />
      <MeetingColumn
        title="Completed meetings"
        variant="completed"
        meetings={completed}
        resultCount={completedResultCount}
        laneTotal={completedLaneTotal}
        sortKey={completedSort}
        onSortChange={onCompletedSortChange}
        projectId={projectId}
        searchQuery={searchQuery}
      />
    </div>
  );
}
