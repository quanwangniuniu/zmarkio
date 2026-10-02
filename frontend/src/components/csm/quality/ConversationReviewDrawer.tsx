'use client';

import React, { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { X } from 'lucide-react';

import CsmQualityAPI from '@/lib/api/csmQualityApi';
import { QualityConversationDetail, QualityRating } from '@/types/csmQuality';
import QualityRatingBadge from './QualityRatingBadge';
import QualityRatingForm from './QualityRatingForm';
import { formatDateTime } from './formatDates';

interface ConversationReviewDrawerProps {
  conversationId: number | null;
  onClose: () => void;
  onSaved: () => void;
}

export function ConversationReviewDrawer({
  conversationId,
  onClose,
  onSaved,
}: ConversationReviewDrawerProps) {
  const [detail, setDetail] = useState<QualityConversationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (conversationId === null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    CsmQualityAPI.getConversation(conversationId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(error?.response?.data?.detail ?? 'Could not load that conversation.');
          onClose();
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, onClose]);

  // Escape closes the drawer, as it would a dialog.
  useEffect(() => {
    if (conversationId === null) return undefined;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [conversationId, onClose]);

  const handleSubmit = useCallback(
    async (rating: QualityRating, comment: string) => {
      if (conversationId === null) return;
      setSaving(true);
      try {
        await CsmQualityAPI.saveReview(conversationId, rating, comment);
        const refreshed = await CsmQualityAPI.getConversation(conversationId);
        setDetail(refreshed);
        toast.success('Annotation saved.');
        onSaved();
      } catch (error) {
        const detailMessage =
          (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        toast.error(detailMessage ?? 'Could not save the annotation.');
      } finally {
        setSaving(false);
      }
    },
    [conversationId, onSaved],
  );

  if (conversationId === null) return null;

  const otherReviews = (detail?.reviews ?? []).filter(
    (review) => review.id !== detail?.my_review?.id,
  );

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="flex-1 bg-slate-900/20" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Review conversation"
        className="flex h-full w-full max-w-xl flex-col overflow-y-auto bg-white shadow-xl"
      >
        <header className="flex items-start justify-between border-b border-slate-200 p-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Review conversation</h2>
            {detail && (
              <p className="mt-0.5 text-sm text-slate-500">
                {detail.customer_name || 'Unknown customer'} · {detail.queue_name || 'No queue'} ·{' '}
                {detail.channel_display}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        {loading && <p className="p-4 text-sm text-slate-500">Loading conversation…</p>}

        {detail && !loading && (
          <>
            <section className="border-b border-slate-200 p-4">
              <h3 className="mb-2 text-sm font-semibold text-slate-700">Transcript</h3>
              <div className="max-h-64 space-y-2 overflow-y-auto rounded-md bg-slate-50 p-3">
                {detail.messages.length === 0 && (
                  <p className="text-sm text-slate-500">No messages on this conversation.</p>
                )}
                {detail.messages.map((message) => (
                  <div key={message.id} className="text-sm">
                    <span className="font-medium text-slate-700">
                      {message.sender_type === 'agent' ? 'Agent' : message.sender_type === 'customer' ? 'Customer' : 'System'}
                    </span>
                    <span className="ml-2 text-xs text-slate-400">
                      {formatDateTime(message.created_at)}
                    </span>
                    <p className="text-slate-600">{message.content}</p>
                  </div>
                ))}
              </div>
            </section>

            <section className="border-b border-slate-200 p-4">
              <h3 className="mb-2 text-sm font-semibold text-slate-700">Your annotation</h3>
              <QualityRatingForm
                existingReview={detail.my_review}
                saving={saving}
                onSubmit={handleSubmit}
              />
            </section>

            {otherReviews.length > 0 && (
              <section className="p-4">
                <h3 className="mb-2 text-sm font-semibold text-slate-700">Other reviewers</h3>
                <ul className="space-y-2">
                  {otherReviews.map((review) => (
                    <li key={review.id} className="rounded-md border border-slate-200 p-2">
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-700">
                          {review.reviewer_name || 'Unknown reviewer'}
                        </span>
                        <QualityRatingBadge rating={review.rating} />
                      </div>
                      {review.comment && (
                        <p className="mt-1 text-sm text-slate-600">{review.comment}</p>
                      )}
                      <p className="mt-1 text-xs text-slate-400">
                        {formatDateTime(review.reviewed_at)}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </aside>
    </div>
  );
}

export default ConversationReviewDrawer;
