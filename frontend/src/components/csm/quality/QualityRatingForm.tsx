'use client';

import React, { useEffect, useState } from 'react';
import {
  ConversationQualityReview,
  QUALITY_RATINGS,
  QUALITY_RATING_LABELS,
  QualityRating,
} from '@/types/csmQuality';
import { RATING_CLASSES } from './QualityRatingBadge';
import { formatDateTime } from './formatDates';

interface QualityRatingFormProps {
  existingReview: ConversationQualityReview | null;
  saving: boolean;
  onSubmit: (rating: QualityRating, comment: string) => void;
}

export function QualityRatingForm({
  existingReview,
  saving,
  onSubmit,
}: QualityRatingFormProps) {
  const [rating, setRating] = useState<QualityRating | null>(existingReview?.rating ?? null);
  const [comment, setComment] = useState(existingReview?.comment ?? '');

  // Re-seed when the drawer switches to a different conversation.
  useEffect(() => {
    setRating(existingReview?.rating ?? null);
    setComment(existingReview?.comment ?? '');
  }, [existingReview?.id, existingReview?.rating, existingReview?.comment]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!rating) return;
    onSubmit(rating, comment);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <span className="block text-sm font-medium text-slate-700">Quality rating</span>
        <div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label="Quality rating">
          {QUALITY_RATINGS.map((value) => {
            const selected = rating === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setRating(value)}
                className={`rounded-full px-3 py-1.5 text-sm font-medium ring-1 ring-inset transition ${
                  selected
                    ? RATING_CLASSES[value]
                    : 'bg-white text-slate-600 ring-slate-300 hover:bg-slate-50'
                }`}
              >
                {QUALITY_RATING_LABELS[value]}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <label htmlFor="quality-comment" className="block text-sm font-medium text-slate-700">
          Comment
        </label>
        <textarea
          id="quality-comment"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          rows={4}
          placeholder="What went well, or what should change next time?"
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-1 focus:ring-[#3CCED7]"
        />
      </div>

      <div className="flex items-center justify-between gap-3">
        {existingReview ? (
          <p className="text-xs text-slate-500">
            Last reviewed by {existingReview.reviewer_name || 'you'} on{' '}
            {formatDateTime(existingReview.reviewed_at)}
          </p>
        ) : (
          <span />
        )}
        <button
          type="submit"
          disabled={!rating || saving}
          className="rounded-md bg-[#3CCED7] px-4 py-2 text-sm font-semibold text-white hover:bg-[#2bb8c1] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? 'Saving…' : existingReview ? 'Update annotation' : 'Save annotation'}
        </button>
      </div>
    </form>
  );
}

export default QualityRatingForm;
