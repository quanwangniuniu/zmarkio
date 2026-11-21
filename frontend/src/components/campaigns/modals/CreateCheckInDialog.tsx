'use client';

import { useState } from 'react';
import { CheckCircle, Minus, AlertCircle, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type { CheckInSentiment } from '@/types/campaign';
import { CampaignAPI } from '@/lib/api/campaignApi';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import { Button } from '@/components/ui/button';
import BrandButton from '../BrandButton';
import { CHECKIN_SENTIMENT } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle } from '@/lib/orgCustomization/optionConfig';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  campaignId: string;
  onSuccess: () => void;
}

const SENTIMENTS: Array<{
  value: CheckInSentiment;
  Icon: typeof CheckCircle;
  tone: string;
  iconTone: string;
}> = [
  {
    value: 'POSITIVE',
    Icon: CheckCircle,
    tone: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
    iconTone: 'text-emerald-600',
  },
  {
    value: 'NEUTRAL',
    Icon: Minus,
    tone: 'bg-gray-50 text-gray-800 ring-gray-200',
    iconTone: 'text-gray-500',
  },
  {
    value: 'NEGATIVE',
    Icon: AlertCircle,
    tone: 'bg-rose-50 text-rose-800 ring-rose-200',
    iconTone: 'text-rose-600',
  },
];

export default function CreateCheckInDialog({ open, onOpenChange, campaignId, onSuccess }: Props) {
  useOptionConfigVersion();
  const [sentiment, setSentiment] = useState<CheckInSentiment | null>(null);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClose = (next: boolean) => {
    if (submitting) return;
    if (!next) {
      setSentiment(null);
      setNote('');
      setError(null);
    }
    onOpenChange(next);
  };

  const handleSubmit = async () => {
    if (!sentiment) {
      setError('Please select a sentiment');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await CampaignAPI.createCheckIn(campaignId, {
        sentiment,
        note: note.trim() || undefined,
      });
      toast.success('Check-in recorded');
      onSuccess();
      handleClose(false);
    } catch (err: any) {
      const msg =
        err?.response?.data?.error ||
        err?.response?.data?.detail ||
        err?.message ||
        'Failed to create check-in';
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <BrandDialog
      open={open}
      onOpenChange={handleClose}
      title="Log Check-in"
      subtitle="Quick sentiment pulse on the campaign's current health."
      width="max-w-md"
    >
      <div className="space-y-4">
        <div>
          <p className="mb-2 text-xs font-medium text-gray-600">
            How is the campaign performing?
          </p>
          <div className="grid grid-cols-3 gap-2">
            {SENTIMENTS.map(({ value, Icon, tone, iconTone }) => {
              const active = sentiment === value;
              return (
                <button
                  key={value}
                  type="button"
                  disabled={submitting}
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
          <label htmlFor="checkin-note" className="mb-1.5 block text-xs font-medium text-gray-600">
            Note (optional)
          </label>
          <textarea
            id="checkin-note"
            rows={3}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Brief observation..."
            disabled={submitting}
            className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:opacity-50"
          />
        </div>

        {error && (
          <div className="rounded-md border border-rose-200 bg-rose-50 p-3">
            <p className="text-xs text-rose-800">{error}</p>
          </div>
        )}
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" disabled={submitting} onClick={() => handleClose(false)}>
          Cancel
        </Button>
        <BrandButton size="sm" disabled={submitting || !sentiment} onClick={handleSubmit}>
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          Record Check-in
        </BrandButton>
      </div>
    </BrandDialog>
  );
}
