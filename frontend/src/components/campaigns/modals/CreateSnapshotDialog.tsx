'use client';

import { useRef, useState } from 'react';
import { Image as ImageIcon, X, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type { CreateSnapshotData, MetricType, MilestoneType } from '@/types/campaign';
import { CampaignAPI } from '@/lib/api/campaignApi';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import { Button } from '@/components/ui/button';
import BrandButton from '../BrandButton';
import { SNAPSHOT_METRIC, SNAPSHOT_MILESTONE } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionChoices } from '@/lib/orgCustomization/optionConfig';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  campaignId: string;
  onSuccess: () => void;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024;

const inputCls =
  'w-full rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:opacity-50';

export default function CreateSnapshotDialog({ open, onOpenChange, campaignId, onSuccess }: Props) {
  useOptionConfigVersion();
  const [milestone, setMilestone] = useState<MilestoneType | ''>('');
  const [spend, setSpend] = useState('');
  const [metric, setMetric] = useState<MetricType | ''>('');
  const [metricValue, setMetricValue] = useState('');
  const [percentage, setPercentage] = useState('');
  const [notes, setNotes] = useState('');
  const [screenshot, setScreenshot] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setMilestone('');
    setSpend('');
    setMetric('');
    setMetricValue('');
    setPercentage('');
    setNotes('');
    setScreenshot(null);
    setPreview(null);
    setError(null);
    if (fileRef.current) fileRef.current.value = '';
  };

  const handleClose = (next: boolean) => {
    if (submitting) return;
    if (!next) reset();
    onOpenChange(next);
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setError('Please select an image file');
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      setError('File size must be under 10MB');
      return;
    }
    setScreenshot(file);
    setError(null);
    const reader = new FileReader();
    reader.onloadend = () => setPreview(reader.result as string);
    reader.readAsDataURL(file);
  };

  const removeScreenshot = () => {
    setScreenshot(null);
    setPreview(null);
    if (fileRef.current) fileRef.current.value = '';
  };

  const handleSubmit = async () => {
    if (!milestone) return setError('Select a milestone type');
    if (!spend || isNaN(parseFloat(spend)) || parseFloat(spend) < 0) {
      return setError('Enter a valid spend amount');
    }
    if (!metric) return setError('Select a metric type');
    if (!metricValue || isNaN(parseFloat(metricValue))) {
      return setError('Enter a valid metric value');
    }
    setSubmitting(true);
    setError(null);
    try {
      const payload: CreateSnapshotData = {
        milestone_type: milestone,
        spend: parseFloat(spend),
        metric_type: metric,
        metric_value: parseFloat(metricValue),
        notes: notes.trim() || undefined,
      };
      if (percentage && !isNaN(parseFloat(percentage))) {
        payload.percentage_change = parseFloat(percentage);
      }
      if (screenshot) payload.screenshot = screenshot;

      await CampaignAPI.createSnapshot(campaignId, payload);
      toast.success('Snapshot captured');
      onSuccess();
      handleClose(false);
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.response?.data?.detail || err?.message || 'Failed to create snapshot');
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = !!milestone && !!spend && !!metric && !!metricValue && !submitting;

  return (
    <BrandDialog
      open={open}
      onOpenChange={handleClose}
      title="Capture Snapshot"
      subtitle="Document performance at a milestone. Spend and primary metric are required."
      width="max-w-2xl"
    >
      <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Milestone Type <span className="text-rose-500">*</span>
          </label>
          <select
            value={milestone}
            onChange={(e) => setMilestone(e.target.value as MilestoneType)}
            disabled={submitting}
            className={inputCls}
          >
            <option value="">Select milestone...</option>
            {optionChoices(SNAPSHOT_MILESTONE).map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Spend (USD) <span className="text-rose-500">*</span>
          </label>
          <input
            type="number"
            step="0.01"
            min="0"
            value={spend}
            onChange={(e) => setSpend(e.target.value)}
            placeholder="0.00"
            disabled={submitting}
            className={inputCls}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">
              Metric Type <span className="text-rose-500">*</span>
            </label>
            <select
              value={metric}
              onChange={(e) => setMetric(e.target.value as MetricType)}
              disabled={submitting}
              className={inputCls}
            >
              <option value="">Select metric...</option>
              {optionChoices(SNAPSHOT_METRIC).map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">
              Metric Value <span className="text-rose-500">*</span>
            </label>
            <input
              type="number"
              step="0.0001"
              value={metricValue}
              onChange={(e) => setMetricValue(e.target.value)}
              placeholder="0.0000"
              disabled={submitting}
              className={inputCls}
            />
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Percentage Change (optional)
          </label>
          <input
            type="number"
            step="0.01"
            value={percentage}
            onChange={(e) => setPercentage(e.target.value)}
            placeholder="e.g. 15.5 or -10.2"
            disabled={submitting}
            className={inputCls}
          />
          <p className="mt-1 text-[11px] text-gray-500">Positive for improvement, negative for decline</p>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Observations (optional)
          </label>
          <textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Free-form notes..."
            disabled={submitting}
            className={inputCls}
          />
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Screenshot (optional)
          </label>
          {preview ? (
            <div className="relative overflow-hidden rounded-md border border-gray-200">
              <img src={preview} alt="Screenshot preview" className="h-auto max-h-64 w-full object-contain" />
              <button
                type="button"
                onClick={removeScreenshot}
                disabled={submitting}
                className="absolute right-2 top-2 rounded-full bg-rose-500 p-1 text-white shadow hover:bg-rose-600 disabled:opacity-50"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <label
              htmlFor="snapshot-file"
              className="flex h-32 w-full cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-gray-300 transition hover:border-[#3CCED7] hover:bg-[#3CCED7]/5"
            >
              <ImageIcon className="mb-2 h-7 w-7 text-gray-400" />
              <p className="text-xs text-gray-600">
                <span className="font-semibold">Click to upload</span> or drag & drop
              </p>
              <p className="text-[11px] text-gray-400">PNG, JPG, GIF up to 10MB</p>
              <input
                id="snapshot-file"
                type="file"
                accept="image/*"
                ref={fileRef}
                onChange={handleFile}
                disabled={submitting}
                className="hidden"
              />
            </label>
          )}
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
        <BrandButton size="sm" disabled={!canSubmit} onClick={handleSubmit}>
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          Capture Snapshot
        </BrandButton>
      </div>
    </BrandDialog>
  );
}
