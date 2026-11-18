'use client';

import { Plus, CalendarPlus, CalendarCheck } from 'lucide-react';
import EmptyState from '@/components/state-feedback/EmptyState';

interface Props {
  variant: 'incoming' | 'completed';
  onCreate?: () => void;
}

export default function MeetingEmptyColumn({ variant, onCreate }: Props) {
  if (variant === 'incoming') {
    return (
      <EmptyState
        className="flex-1 rounded-xl border-2 border-dashed border-gray-200 bg-white/50"
        icon={<CalendarPlus className="h-5 w-5" />}
        title="No incoming meetings"
        description="Create your first meeting to start planning."
        actionLabel="Create meeting"
        onAction={onCreate}
        actionIcon={<Plus className="h-3.5 w-3.5" />}
      />
    );
  }
  return (

    <EmptyState
      className="flex-1 rounded-xl border-2 border-dashed border-gray-200 bg-white/50"
      icon={<CalendarCheck className="h-5 w-5" />}
      title="No completed meetings"
      description="Meetings with a scheduled day before today appear here."
    />

  );
}
