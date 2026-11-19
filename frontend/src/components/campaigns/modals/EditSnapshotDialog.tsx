'use client';

import { useEffect, useRef, useState } from 'react';
import { Image as ImageIcon, X, Loader2, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type {
  MetricType,
  MilestoneType,
  PerformanceSnapshot,
  UpdateSnapshotData,
} from '@/types/campaign';
import { CampaignAPI } from '@/lib/api/campaignApi';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import { Button } from '@/components/ui/button';
import BrandButton from '../BrandButton';
import { SNAPSHOT_METRIC, SNAPSHOT_MILESTONE } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionChoices } from '@/lib/orgCustomization/optionConfig';
import BrandConfirmDialog from '../BrandConfirmDialog';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  campaignId: string;
  snapshot: PerformanceSnapshot | null;
  onSuccess: () => void;
  onDelete?: () => void;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024;

const inputCls =
  'w-full rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:opacity-50';

export default function EditSnapshotDialog({
  open,
  onOpenChange,
  campaignId,
  snapshot,
  onSuccess,
  onDelete,
}: Props) {
  useOptionConfigVersion();
  const [milestone, setMilestone] = useState<MilestoneType>('CUSTOM');
  const [spend, setSpend] = useState('');
  const [metric, setMetric] = useState<MetricType>('CPA');
  const [metricValue, setMetricValue] = useState('');
  const [percentage, setPercentage] = useState('');
  const [notes, setNotes] = useState('');
  const [newScreenshot, setNewScreenshot] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const busy = submitting || deleting;

  useEffect(() => {
    if (snapshot && open) {
      setMilestone(snapshot.milestone_type);
      setSpend(snapshot.spend);
      setMetric(snapshot.metric_type);
      setMetricValue(snapshot.metric_value);
      setPercentage(snapshot.percentage_change || '');
      setNotes(snapshot.notes || '');
      setNewScreenshot(null);
      setPreview(snapshot.screenshot_url || null);
      setError(null);
    }
  }, [snapshot, open]);

  const handleClose = (next: boolean) => {
    if (busy) return;
    onOpenChange(next);
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) return setError('Please select an image file');
    if (file.size > MAX_FILE_SIZE) return setError('File size must be under 10MB');
    setNewScreenshot(file);
    setError(null);
    const reader = new FileReader();
    reader.onloadend = () => setPreview(reader.result as string);
    reader.readAsDataURL(file);
  };

  const removeScreenshot = () => {
    setNewScreenshot(null);
    setPreview(null);
    if (fileRef.current) fileRef.current.value = '';
  };

  const handleSubmit = async () => {
    if (!snapshot) return;
    if (!spend || isNaN(parseFloat(spend))) return setError('Enter a valid spend amount');
    if (!metricValue || isNaN(parseFloat(metricValue))) {
      return setError('Enter a valid metric value');
    }
    setSubmitting(true);
    setError(null);
    try {
      const payload: UpdateSnapshotData = {
        milestone_type: milestone,
        spend: parseFloat(spend),
        metric_type: metric,
        metric_value: parseFloat(metricValue),
        notes: notes.trim() || undefined,
      };
      if (percentage && !isNaN(parseFloat(percentage))) {
        payload.percentage_change = parseFloat(percentage);
      }
      if (newScreenshot) payload.screenshot = newScreenshot;

      await CampaignAPI.updateSnapshot(campaignId, snapshot.id, payload);
      toast.success('Snapshot updated');
      onSuccess();
      handleClose(false);
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.message || 'Failed to update snapshot');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!snapshot) return;
    setDeleting(true);
    setError(null);
    try {
      await CampaignAPI.deleteSnapshot(campaignId, snapshot.id);
      toast.success('Snapshot deleted');
      onDelete?.();
      setConfirmDelete(false);
      handleClose(false);
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.message || 'Failed to delete snapshot');
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  };

  if (!snapshot) return null;

  return (
    <>
      <BrandDialog
        open={open}
        onOpenChange={handleClose}
        title="Edit Snapshot"
        subtitle="Update milestone metrics or attachments."
        width="max-w-2xl"
      >
        <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">Milestone Type</label>
            <select
              value={milestone}
              onChange={(e) => setMilestone(e.target.value as MilestoneType)}
              disabled={busy}
              className={inputCls}
            >
              {optionChoices(SNAPSHOT_MILESTONE, snapshot?.milestone_type).map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">Spend (USD)</label>
            <input
              type="number"
              step="0.01"
              min="0"
              value={spend}
              onChange={(e) => setSpend(e.target.value)}
              disabled={busy}
              className={inputCls}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">Metric Type</label>
              <select
                value={metric}
                onChange={(e) => setMetric(e.target.value as MetricType)}
                disabled={busy}
                className={inputCls}
              >
                {optionChoices(SNAPSHOT_METRIC, snapshot?.metric_type).map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">Metric Value</label>
              <input
                type="number"
                step="0.0001"
                value={metricValue}
                onChange={(e) => setMetricValue(e.target.value)}
                disabled={busy}
                className={inputCls}
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">Percentage Change</label>
            <input
              type="number"
              step="0.01"
              value={percentage}
              onChange={(e) => setPercentage(e.target.value)}
              disabled={busy}
              className={inputCls}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">Observations</label>
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={busy}
              className={inputCls}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">Screenshot</label>
            {preview ? (
              <div className="relative overflow-hidden rounded-md border border-gray-200">
                <img src={preview} alt="Snapshot preview" className="h-auto max-h-64 w-full object-contain" />
                <button
                  type="button"
                  onClick={removeScreenshot}
                  disabled={busy}
                  className="absolute right-2 top-2 rounded-full bg-rose-500 p-1 text-white shadow hover:bg-rose-600 disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <label
                htmlFor="edit-snapshot-file"
                className="flex h-32 w-full cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-gray-300 transition hover:border-[#3CCED7] hover:bg-[#3CCED7]/5"
              >
                <ImageIcon className="mb-2 h-7 w-7 text-gray-400" />
                <p className="text-xs text-gray-600">
                  <span className="font-semibold">Click to upload</span> replacement
                </p>
                <input
                  id="edit-snapshot-file"
                  type="file"
                  accept="image/*"
                  ref={fileRef}
                  onChange={handleFile}
                  disabled={busy}
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
            <BrandButton size="sm" disabled={busy} onClick={handleSubmit}>
              {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
              Save Changes
            </BrandButton>
          </div>
        </div>
      </BrandDialog>

      <BrandConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete Snapshot?"
        message="This action cannot be undone."
        confirmLabel="Delete"
        tone="danger"
        loading={deleting}
        onConfirm={handleDelete}
      />
    </>
  );
}
