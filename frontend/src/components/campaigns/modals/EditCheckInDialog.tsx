'use client';

import { useEffect, useState } from 'react';
import { CheckCircle, Minus, AlertCircle, Loader2, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type { CampaignCheckIn, CheckInSentiment } from '@/types/campaign';
import { CampaignAPI } from '@/lib/api/campaignApi';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import { Button } from '@/components/ui/button';
import BrandButton from '../BrandButton';
import { CHECKIN_SENTIMENT } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle } from '@/lib/orgCustomization/optionConfig';
import BrandConfirmDialog from '../BrandConfirmDialog';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  campaignId: string;
  checkIn: CampaignCheckIn | null;
  onSuccess: () => void;
}

const SENTIMENTS: Array<{
  value: CheckInSentiment;
  Icon: typeof CheckCircle;
  tone: string;
  iconTone: string;
}> = [
  { value: 'POSITIVE', Icon: CheckCircle, tone: 'bg-emerald-50 text-emerald-800 ring-emerald-200', iconTone: 'text-emerald-600' },
  { value: 'NEUTRAL',   Icon: Minus,       tone: 'bg-gray-50 text-gray-800 ring-gray-200',            iconTone: 'text-gray-500' },
  { value: 'NEGATIVE', Icon: AlertCircle, tone: 'bg-rose-50 text-rose-800 ring-rose-200',            iconTone: 'text-rose-600' },
];

export default function EditCheckInDialog({ open, onOpenChange, campaignId, checkIn, onSuccess }: Props) {
  useOptionConfigVersion();
  const [sentiment, setSentiment] = useState<CheckInSentiment | null>(null);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (checkIn && open) {
      setSentiment(checkIn.sentiment);
      setNote(checkIn.note || '');
      setError(null);
    }
  }, [checkIn, open]);

  const busy = submitting || deleting;

  const handleClose = (next: boolean) => {
    if (busy) return;
    if (!next) {
      setSentiment(null);
      setNote('');
      setError(null);
    }
    onOpenChange(next);
  };

  const handleSubmit = async () => {
    if (!sentiment || !checkIn) {
      setError('Please select a sentiment');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await CampaignAPI.updateCheckIn(campaignId, checkIn.id, {
        sentiment,
        note: note.trim() || undefined,
      });
      toast.success('Check-in updated');
      onSuccess();
      handleClose(false);
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.message || 'Failed to update check-in');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!checkIn) return;
    setDeleting(true);
    setError(null);
    try {
      await CampaignAPI.deleteCheckIn(campaignId, checkIn.id);
      toast.success('Check-in deleted');
      onSuccess();
      setConfirmDelete(false);
      handleClose(false);
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.message || 'Failed to delete check-in');
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  };

  if (!checkIn) return null;

  return (
    <>
      <BrandDialog
        open={open}
        onOpenChange={handleClose}
        title="Edit Check-in"
        subtitle="Update sentiment or note."
        width="max-w-md"
      >
        <div className="space-y-4">
          <div>
            <p className="mb-2 text-xs font-medium text-gray-600">Sentiment</p>
            <div className="grid grid-cols-3 gap-2">
              {SENTIMENTS.map(({ value, Icon, tone, iconTone }) => {
                const active = sentiment === value;
                return (
                  <button
                    key={value}
                    type="button"
                    disabled={busy}
                    onClick={() => setSentiment(value)}
                    style={active ? optionColorStyle(CHECKIN_SENTIMENT.colors[value]) : undefined}
                    className={`flex flex-col items-center gap-1.5 rounded-lg border px-3 py-3 text-xs font-medium transition disabled:opacity-50 ${
                      active
                        ? `${tone} ring-1`
                        : 'bg-white text-gray-700 border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <Icon className={`h-5 w-5 ${active ? iconTone : 'text-gray-400'}`} />
                    {CHECKIN_SENTIMENT.labels[value]}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label htmlFor="edit-checkin-note" className="mb-1.5 block text-xs font-medium text-gray-600">
              Note (optional)
            </label>
            <textarea
              id="edit-checkin-note"
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={busy}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:opacity-50"
            />
          </div>

          {error && (
            <div className="rounded-md border border-rose-200 bg-rose-50 p-3">
              <p className="text-xs text-rose-800">{error}</p>
            </div>
          )}
        </div>

        <div className="mt-5 flex items-center justify-between">
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => setConfirmDelete(true)}
            className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
          >
            <Trash2 className="h-4 w-4" />
            Delete
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={busy} onClick={() => handleClose(false)}>
              Cancel
            </Button>
            <BrandButton size="sm" disabled={busy || !sentiment} onClick={handleSubmit}>
              {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
              Save Changes
            </BrandButton>
          </div>
        </div>
      </BrandDialog>

      <BrandConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete Check-in?"
        message="This action cannot be undone."
        confirmLabel="Delete"
        tone="danger"
        loading={deleting}
        onConfirm={handleDelete}
      />
    </>
  );
}
