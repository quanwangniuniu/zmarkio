'use client';

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import { REVIEW_QUALITY } from '@/lib/decisions/decisionConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle } from '@/lib/orgCustomization/optionConfig';

type Quality = 'GOOD' | 'ACCEPTABLE' | 'POOR';

const QUALITY_OPTIONS: { value: Quality; bg: string; text: string }[] = [
  { value: 'GOOD', bg: 'bg-emerald-50', text: 'text-emerald-700' },
  { value: 'ACCEPTABLE', bg: 'bg-sky-50', text: 'text-sky-700' },
  { value: 'POOR', bg: 'bg-rose-50', text: 'text-rose-700' },
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (payload: {
    outcomeText: string;
    reflectionText: string;
    decisionQuality: Quality;
  }) => Promise<void>;
}

export default function DecisionReviewDialog({ open, onOpenChange, onConfirm }: Props) {
  useOptionConfigVersion();
  const [outcomeText, setOutcomeText] = useState('');
  const [reflectionText, setReflectionText] = useState('');
  const [quality, setQuality] = useState<Quality>('ACCEPTABLE');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setOutcomeText('');
      setReflectionText('');
      setQuality('ACCEPTABLE');
    }
  }, [open]);

  const valid = outcomeText.trim().length > 0 && reflectionText.trim().length > 0;

  const handleConfirm = async () => {
    if (!valid) return;
    setBusy(true);
    try {
      await onConfirm({
        outcomeText: outcomeText.trim(),
        reflectionText: reflectionText.trim(),
        decisionQuality: quality,
      });
      onOpenChange(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <BrandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add review"
      subtitle="Capture the outcome, reflection, and overall quality of this decision."
    >
      <div className="space-y-3">
        <div>
          <label className="text-[11px] font-medium uppercase tracking-wide text-gray-500">
            Outcome
          </label>
          <textarea
            value={outcomeText}
            onChange={(e) => setOutcomeText(e.target.value)}
            rows={3}
            placeholder="What actually happened after this decision?"
            className="mt-1 w-full resize-y rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </div>
        <div>
          <label className="text-[11px] font-medium uppercase tracking-wide text-gray-500">
            Reflection
          </label>
          <textarea
            value={reflectionText}
            onChange={(e) => setReflectionText(e.target.value)}
            rows={3}
            placeholder="What would you do differently?"
            className="mt-1 w-full resize-y rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </div>
        <div>
          <label className="text-[11px] font-medium uppercase tracking-wide text-gray-500">
            Decision quality
          </label>
          <div className="mt-1.5 flex gap-2">
            {QUALITY_OPTIONS.map((opt) => {
              const active = quality === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setQuality(opt.value)}
                  className={`inline-flex items-center rounded-full px-3 py-1 text-[12px] font-medium transition ${
                    active
                      ? `${opt.bg} ${opt.text} ring-1 ring-current/30`
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                  style={active ? optionColorStyle(REVIEW_QUALITY.colors[opt.value]) : undefined}
                >
                  {REVIEW_QUALITY.labels[opt.value]}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          disabled={busy}
          className="inline-flex h-9 items-center justify-center rounded-lg bg-white px-4 text-sm font-medium text-gray-700 ring-1 ring-gray-200 transition hover:ring-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleConfirm}
          disabled={busy || !valid}
          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-4 text-sm font-medium text-white shadow-sm transition hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Submit review
        </button>
      </div>
    </BrandDialog>
  );
}
