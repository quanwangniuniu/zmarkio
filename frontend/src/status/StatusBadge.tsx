"use client";

import React from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { StatusTone } from './statusTypes';

const toneClasses: Record<StatusTone, string> = {
  todo: 'bg-gray-200 text-gray-900',
  in_progress: 'bg-blue-600 text-white',
  in_review: 'bg-indigo-600 text-white',
  done: 'bg-green-700 text-white',
  default: 'bg-gray-200 text-gray-900',
};

export interface StatusBadgeProps {
  label: string;
  tone?: StatusTone;
  className?: string;
  showChevron?: boolean;
}

const StatusBadge: React.FC<StatusBadgeProps> = ({
  label,
  tone = 'default',
  className,
  showChevron = false,
}) => {
  return (
    <span
      className={cn(
        'inline-flex items-center justify-between gap-1 rounded-sm px-2.5 py-1 text-xs font-semibold uppercase tracking-wide',
        toneClasses[tone],
        className
      )}
    >
      {label}
      {showChevron && <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />}
    </span>
  );
};

export default StatusBadge;
