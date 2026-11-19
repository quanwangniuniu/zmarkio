'use client';

import { useEffect, useMemo, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import type {
  CampaignObjective,
  CampaignPlatform,
  CreateCampaignData,
} from '@/types/campaign';
import { CampaignAPI } from '@/lib/api/campaignApi';
import { ProjectAPI } from '@/lib/api/projectApi';
import { useProjectStore } from '@/lib/projectStore';
import UserPicker, { User } from '@/people/UserPicker';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import BrandButton from '../BrandButton';
import { CAMPAIGN_OBJECTIVE, CAMPAIGN_PLATFORM } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionChoices } from '@/lib/orgCustomization/optionConfig';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}

const inputCls =
  'w-full rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:opacity-50';

const todayIso = () => new Date().toISOString().split('T')[0];

export default function CreateCampaignDialog({ open, onOpenChange, onSuccess }: Props) {
  useOptionConfigVersion();
  const activeProject = useProjectStore((s) => s.activeProject);
  const [form, setForm] = useState<Partial<CreateCampaignData>>({
    name: '',
    objective: undefined,
    platforms: [],
    start_date: todayIso(),
  });
  const [users, setUsers] = useState<User[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof CreateCampaignData>(key: K, value: CreateCampaignData[K] | undefined) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  useEffect(() => {
    if (!open) return;
    if (!activeProject?.id) return;
    let cancelled = false;
    setLoadingUsers(true);
    ProjectAPI.getProjectMembers(activeProject.slug ?? activeProject.id)
      .then((members) => {
        if (cancelled) return;
        setUsers(
          members.map((m) => ({
            id: m.user.id,
            name: m.user.username || m.user.email || 'Unknown',
            email: m.user.email || '',
          }))
        );
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingUsers(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, activeProject?.id]);

  useEffect(() => {
    if (open) {
      setForm({
        name: '',
        objective: undefined,
        platforms: [],
        start_date: todayIso(),
      });
      setError(null);
    }
  }, [open]);

  const togglePlatform = (p: CampaignPlatform) => {
    const cur = form.platforms || [];
    set(
      'platforms',
      cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p]
    );
  };

  const handleTagKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter' || !e.currentTarget.value.trim()) return;
    e.preventDefault();
    const tag = e.currentTarget.value.trim();
    const cur = form.tags || [];
    if (!cur.includes(tag)) set('tags', [...cur, tag]);
    e.currentTarget.value = '';
  };

  const removeTag = (tag: string) => {
    set('tags', (form.tags || []).filter((t) => t !== tag));
  };

  const handleClose = (next: boolean) => {
    if (submitting) return;
    onOpenChange(next);
  };

  const canSubmit = useMemo(() => {
    return (
      !!form.name?.trim() &&
      !!form.objective &&
      !!form.platforms?.length &&
      !!form.start_date &&
      !!form.owner_id &&
      !!activeProject?.id &&
      !submitting
    );
  }, [form, activeProject?.id, submitting]);

  const handleSubmit = async () => {
    if (!activeProject?.id) return setError('No active project. Switch project first.');
    if (!form.name?.trim()) return setError('Name is required');
    if (!form.objective) return setError('Objective is required');
    if (!form.platforms?.length) return setError('Select at least one platform');
    if (!form.start_date) return setError('Start date is required');
    if (!form.owner_id) return setError('Owner is required');

    setSubmitting(true);
    setError(null);
    try {
      const payload: CreateCampaignData = {
        name: form.name.trim(),
        objective: form.objective,
        platforms: form.platforms,
        start_date: form.start_date,
        end_date: form.end_date,
        owner_id: form.owner_id,
        project_id: activeProject.id,
        hypothesis: form.hypothesis?.trim() || undefined,
        tags: form.tags,
        budget_estimate: form.budget_estimate,
      };
      await CampaignAPI.createCampaign(payload);
      toast.success('Campaign created');
      onSuccess?.();
      handleClose(false);
    } catch (err: any) {
      setError(
        err?.response?.data?.error ||
          err?.response?.data?.detail ||
          err?.message ||
          'Failed to create campaign'
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <BrandDialog
      open={open}
      onOpenChange={handleClose}
      title="Create Campaign"
      subtitle={
        activeProject
          ? `In project "${activeProject.name}"`
          : 'Pick a project in the shell first'
      }
      width="max-w-2xl"
    >
      <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Campaign Name <span className="text-rose-500">*</span>
          </label>
          <input
            type="text"
            value={form.name || ''}
            onChange={(e) => set('name', e.target.value)}
            disabled={submitting}
            placeholder="e.g. Q2 Awareness Push"
            className={inputCls}
          />
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Objective <span className="text-rose-500">*</span>
          </label>
          <select
            value={form.objective || ''}
            onChange={(e) => set('objective', e.target.value as CampaignObjective)}
            disabled={submitting}
            className={inputCls}
          >
            <option value="">Select objective...</option>
            {optionChoices<CampaignObjective>(CAMPAIGN_OBJECTIVE).map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Platforms <span className="text-rose-500">*</span>
          </label>
          <div className="flex flex-wrap gap-1.5">
            {optionChoices<CampaignPlatform>(CAMPAIGN_PLATFORM).map((p) => {
              const active = form.platforms?.includes(p.value);
              return (
                <button
                  key={p.value}
                  type="button"
                  disabled={submitting}
                  onClick={() => togglePlatform(p.value)}
                  className={`rounded-md border px-2.5 py-1 text-xs transition ${
                    active
                      ? 'bg-[#3CCED7]/10 border-[#3CCED7]/40 text-[#0E8A96]'
                      : 'bg-white border-gray-200 text-gray-700 hover:border-gray-300'
                  }`}
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">
            Owner <span className="text-rose-500">*</span>
          </label>
          <UserPicker
            users={users}
            value={form.owner_id || null}
            onChange={(userId) => set('owner_id', userId ? Number(userId) : undefined)}
            placeholder={loadingUsers ? 'Loading members...' : 'Select owner'}
            disabled={submitting || loadingUsers || !activeProject?.id}
            loading={loadingUsers}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">
              Start Date <span className="text-rose-500">*</span>
            </label>
            <input
              type="date"
              value={form.start_date || ''}
              onChange={(e) => set('start_date', e.target.value)}
              disabled={submitting}
              className={inputCls}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">End Date</label>
            <input
              type="date"
              value={form.end_date || ''}
              onChange={(e) => set('end_date', e.target.value || undefined)}
              min={form.start_date || undefined}
              disabled={submitting}
              className={inputCls}
            />
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">Hypothesis</label>
          <textarea
            rows={3}
            value={form.hypothesis || ''}
            onChange={(e) => set('hypothesis', e.target.value || undefined)}
            disabled={submitting}
            placeholder="Optional strategic hypothesis..."
            className={inputCls}
          />
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">Tags</label>
          {form.tags && form.tags.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {form.tags.map((tag) => (
                <Badge key={tag} variant="secondary" className="gap-1">
                  {tag}
                  <button
                    type="button"
                    onClick={() => removeTag(tag)}
                    disabled={submitting}
                    className="ml-0.5 hover:text-rose-600"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
          <input
            type="text"
            onKeyDown={handleTagKey}
            disabled={submitting}
            placeholder="Type a tag and press Enter"
            className={inputCls}
          />
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-gray-600">Budget Estimate</label>
          <input
            type="number"
            min="0"
            step="0.01"
            value={form.budget_estimate ?? ''}
            onChange={(e) =>
              set('budget_estimate', e.target.value ? Number(e.target.value) : undefined)
            }
            disabled={submitting}
            placeholder="0.00"
            className={inputCls}
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
        <BrandButton size="sm" disabled={!canSubmit} onClick={handleSubmit}>
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          Create Campaign
        </BrandButton>
      </div>
    </BrandDialog>
  );
}
