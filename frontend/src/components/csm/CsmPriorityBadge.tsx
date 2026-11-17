'use client';

import type { TicketPriority } from '@/types/csm';
import { PRIORITY_LABELS } from '@/types/csm';
import { CSM_PRIORITY } from '@/lib/csm/csmConfig';

const BADGE_STYLES: Record<TicketPriority, string> = {
  critical: 'bg-red-50 text-red-700 ring-red-200',
  high:     'bg-orange-50 text-orange-700 ring-orange-200',
  medium:   'bg-blue-50 text-blue-700 ring-blue-200',
  low:      'bg-gray-100 text-gray-500 ring-gray-200',
};

const DOT_STYLES: Record<TicketPriority, string> = {
  critical: 'bg-red-500',
  high:     'bg-orange-400',
  medium:   'bg-blue-400',
  low:      'bg-gray-400',
};

interface CsmPriorityBadgeProps {
  priority: string;
  size?: 'sm' | 'md';
}

export function CsmPriorityBadge({ priority, size = 'sm' }: CsmPriorityBadgeProps) {
  const p = priority as TicketPriority;
  const badgeStyle = BADGE_STYLES[p] ?? 'bg-gray-100 text-gray-500 ring-gray-200';
  const dotStyle   = DOT_STYLES[p]   ?? 'bg-gray-400';
  const label      = PRIORITY_LABELS[p] ?? priority;
  const color      = CSM_PRIORITY.colors[priority];

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full ring-1 ring-inset font-medium shrink-0 ${badgeStyle} ${
        size === 'md' ? 'px-2 py-0.5 text-xs' : 'px-1.5 py-0.5 text-[11px]'
      }`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${color ? '' : dotStyle}`}
        style={color ? { backgroundColor: color } : undefined}
      />
      {label}
    </span>
  );
}
