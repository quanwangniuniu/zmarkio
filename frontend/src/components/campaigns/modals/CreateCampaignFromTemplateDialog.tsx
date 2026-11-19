'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type {
  CampaignObjective,
  CampaignPlatform,
  CampaignTemplate,
  CreateCampaignFromTemplateData,
} from '@/types/campaign';
import { CampaignAPI } from '@/lib/api/campaignApi';
import { ProjectAPI } from '@/lib/api/projectApi';
import { useProjectStore } from '@/lib/projectStore';
import { useBuildUrl } from '@/lib/buildUrl';
import UserPicker, { User } from '@/people/UserPicker';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import { Button } from '@/components/ui/button';
import BrandButton from '../BrandButton';
import { CAMPAIGN_OBJECTIVE, CAMPAIGN_PLATFORM } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionChoices } from '@/lib/orgCustomization/optionConfig';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  template?: CampaignTemplate | null;
}

const inputCls =
  'w-full rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:opacity-50';

const todayIso = () => new Date().toISOString().split('T')[0];

export default function CreateCampaignFromTemplateDialog({
  open,
  onOpenChange,
  template: providedTemplate,
}: Props) {
  useOptionConfigVersion();
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const activeProject = useProjectStore((s) => s.activeProject);
  const [templates, setTemplates] = useState<CampaignTemplate[]>([]);
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const [selected, setSelected] = useState<CampaignTemplate | null>(providedTemplate || null);

  const [name, setName] = useState('');
  const [ownerId, setOwnerId] = useState<number | null>(null);
  const [startDate, setStartDate] = useState(todayIso());
  const [endDate, setEndDate] = useState('');
  const [objective, setObjective] = useState<CampaignObjective | undefined>(undefined);
  const [platforms, setPlatforms] = useState<CampaignPlatform[]>([]);
  const [hypothesis, setHypothesis] = useState('');
  const [budget, setBudget] = useState<number | undefined>(undefined);
  const [users, setUsers] = useState<User[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || providedTemplate) return;
    setLoadingTemplates(true);
    CampaignAPI.getTemplates()
      .then((res) => {
        const list = Array.isArray(res.data)
          ? res.data
          : (res.data as any)?.results || [];
        setTemplates(list);
      })
      .catch(() => setTemplates([]))
      .finally(() => setLoadingTemplates(false));
  }, [open, providedTemplate]);

  useEffect(() => {
    if (!open) return;
    const tmpl = providedTemplate || null;
    setSelected(tmpl);
    setName(tmpl ? `${tmpl.name} Campaign` : '');
    setObjective(tmpl?.objective);
    setPlatforms(tmpl?.platforms || []);
    setHypothesis(tmpl?.hypothesis_framework || '');
    setOwnerId(null);
    setStartDate(todayIso());
    setEndDate('');
    setBudget(undefined);
    setError(null);
  }, [open, providedTemplate]);

  useEffect(() => {
    if (!selected || providedTemplate) return;
    setName(`${selected.name} Campaign`);
    setObjective(selected.objective);
    setPlatforms(selected.platforms || []);
    setHypothesis(selected.hypothesis_framework || '');
  }, [selected, providedTemplate]);

  useEffect(() => {
    if (!open || !activeProject?.id) return;
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

  const togglePlatform = (p: CampaignPlatform) => {
    setPlatforms((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]
    );
  };

  const handleClose = (next: boolean) => {
    if (submitting) return;
    onOpenChange(next);
  };

  const canSubmit = useMemo(
    () =>
      !!selected &&
      !!name.trim() &&
      !!ownerId &&
      !!startDate &&
      !!activeProject?.id &&
      !submitting,
    [selected, name, ownerId, startDate, activeProject?.id, submitting]
  );

  const handleSubmit = async () => {
    if (!selected) return setError('Select a template');
    if (!activeProject?.id) return setError('No active project.');
    if (!name.trim()) return setError('Name is required');
    if (!ownerId) return setError('Owner is required');
    if (!startDate) return setError('Start date is required');

    setSubmitting(true);
    setError(null);
    try {
      const payload: CreateCampaignFromTemplateData = {
        name: name.trim(),
        project: activeProject.id as any,
        owner: ownerId as any,
        start_date: startDate,
        end_date: endDate || undefined,
        objective: objective || undefined,
        platforms: platforms.length ? platforms : undefined,
        hypothesis: hypothesis.trim() || undefined,
        budget_estimate: budget,
      };
      const res = await CampaignAPI.createCampaignFromTemplate(selected.id, payload);
      toast.success('Campaign created');
      handleClose(false);
      router.push(buildUrl(`/campaigns/${res.data.slug}`));
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
      title="Create from Template"
      subtitle={
        activeProject
          ? `In project "${activeProject.name}"`
          : 'Pick a project in the shell first'
      }
      width="max-w-2xl"
    >
      <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
        {!providedTemplate && (
          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">
              Template <span className="text-rose-500">*</span>
            </label>
            <select
              value={selected?.id || ''}
              onChange={(e) => {
                const t = templates.find((x) => x.id === e.target.value);
                setSelected(t || null);
              }}
              disabled={submitting || loadingTemplates}
              className={inputCls}
            >
              <option value="">
                {loadingTemplates ? 'Loading templates...' : 'Select template...'}
              </option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} (v{t.version_number})
                </option>
              ))}
            </select>
          </div>
        )}

        {selected && (
          <>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">
                Campaign Name <span className="text-rose-500">*</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={submitting}
                className={inputCls}
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">Objective</label>
              <select
                value={objective || ''}
                onChange={(e) => setObjective(e.target.value as CampaignObjective)}
                disabled={submitting}
                className={inputCls}
              >
                <option value="">(inherit from template)</option>
                {optionChoices(CAMPAIGN_OBJECTIVE, objective).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">Platforms</label>
              <div className="flex flex-wrap gap-1.5">
                {optionChoices<CampaignPlatform>(CAMPAIGN_PLATFORM, platforms).map(({ value: p, label }) => {
                  const active = platforms.includes(p);
                  return (
                    <button
                      key={p}
                      type="button"
                      disabled={submitting}
                      onClick={() => togglePlatform(p)}
                      className={`rounded-md border px-2.5 py-1 text-xs transition ${
                        active
                          ? 'bg-[#3CCED7]/10 border-[#3CCED7]/40 text-[#0E8A96]'
                          : 'bg-white border-gray-200 text-gray-700 hover:border-gray-300'
                      }`}
                    >
                      {label}
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
                value={ownerId}
                onChange={(uid) => setOwnerId(uid ? Number(uid) : null)}
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
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  disabled={submitting}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-gray-600">End Date</label>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  min={startDate || undefined}
                  disabled={submitting}
                  className={inputCls}
                />
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">Hypothesis</label>
              <textarea
                rows={3}
                value={hypothesis}
                onChange={(e) => setHypothesis(e.target.value)}
                disabled={submitting}
                className={inputCls}
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">
                Budget Estimate
              </label>
              <input
                type="number"
                min="0"
                step="0.01"
                value={budget ?? ''}
                onChange={(e) =>
                  setBudget(e.target.value ? Number(e.target.value) : undefined)
                }
                disabled={submitting}
                placeholder="0.00"
                className={inputCls}
              />
            </div>
          </>
        )}

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
          Create from Template
        </BrandButton>
      </div>
    </BrandDialog>
  );
}
