'use client';

import React from 'react';
import { QUALITY_RATING_LABELS, QualityRating } from '@/types/csmQuality';

/** One colour per rating, shared by the table, the drawer and the report. */
export const RATING_CLASSES: Record<QualityRating, string> = {
  good: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  needs_improvement: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  poor: 'bg-rose-50 text-rose-700 ring-rose-600/20',
};

interface QualityRatingBadgeProps {
  rating?: QualityRating | null;
  className?: string;
}

export function QualityRatingBadge({ rating, className = '' }: QualityRatingBadgeProps) {
  if (!rating) {
    return (
      <span className={`inline-flex items-center rounded-full bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-500 ring-1 ring-inset ring-slate-500/20 ${className}`}>
        Not reviewed
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${RATING_CLASSES[rating]} ${className}`}
    >
      {QUALITY_RATING_LABELS[rating]}
    </span>
  );
}

export default QualityRatingBadge;
