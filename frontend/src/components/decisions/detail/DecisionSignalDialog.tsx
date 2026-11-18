'use client';

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import InlineSelect from '@/components/tasks/detail/InlineSelect';
import { SIGNAL_METRIC, SIGNAL_MOVEMENT, SIGNAL_PERIOD } from '@/lib/decisions/decisionConfig';
import { optionChoices, preferredOption } from '@/lib/orgCustomization/optionConfig';
import type {
  DecisionSignal,
  SignalComparison,
  SignalDeltaUnit,
  SignalMetric,
  SignalMovement,
  SignalPeriod,
  SignalScopeType,
} from '@/types/decision';

const COMPARISON_OPTIONS: { value: SignalComparison; label: string }[] = [
  { value: 'NONE', label: 'None' },
  { value: 'PREVIOUS_PERIOD', label: 'Previous period' },
  { value: 'SAME_PERIOD_LAST_WEEK', label: 'Same period last week' },
  { value: 'SINCE_LAUNCH', label: 'Since launch' },
];

const SCOPE_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: '__unassigned__', label: 'Unassigned' },
  { value: 'CAMPAIGN', label: 'Campaign' },
  { value: 'AD_SET', label: 'Ad set' },
  { value: 'AD', label: 'Ad' },
  { value: 'CHANNEL', label: 'Channel' },
  { value: 'AUDIENCE', label: 'Audience' },
  { value: 'REGION', label: 'Region' },
];

const DELTA_UNIT_OPTIONS: { value: string; label: string }[] = [
  { value: '__unassigned__', label: 'Unassigned' },
  { value: 'PERCENT', label: 'Percent' },
  { value: 'CURRENCY', label: 'Currency' },
  { value: 'ABSOLUTE', label: 'Absolute' },
];

export interface SignalFormPayload {
  metric: SignalMetric;
  movement: SignalMovement;
  period: SignalPeriod;
  comparison: SignalComparison;
  scopeType: SignalScopeType | null;
  scopeValue: string | null;
  deltaValue: number | null;
  deltaUnit: SignalDeltaUnit | null;
  displayTextOverride: string | null;
}

interface Props {
  open: boolean;
  mode: 'create' | 'edit';
  initial?: DecisionSignal | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: SignalFormPayload) => Promise<void>;
}

export default function DecisionSignalDialog({
  open,
  mode,
  initial,
  onOpenChange,
  onSubmit,
}: Props) {
  const [metric, setMetric] = useState<SignalMetric>('ROAS');
  const [movement, setMovement] = useState<SignalMovement>('NO_SIGNIFICANT_CHANGE');
  const [period, setPeriod] = useState<SignalPeriod>('LAST_7_DAYS');
  const [comparison, setComparison] = useState<SignalComparison>('NONE');
  const [scopeType, setScopeType] = useState<string>('__unassigned__');
  const [scopeValue, setScopeValue] = useState<string>('');
  const [deltaValue, setDeltaValue] = useState<string>('');
  const [deltaUnit, setDeltaUnit] = useState<string>('__unassigned__');
  const [displayTextOverride, setDisplayTextOverride] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    if (mode === 'edit' && initial) {
      setMetric(initial.metric);
      setMovement(initial.movement);
      setPeriod(initial.period);
      setComparison(initial.comparison ?? 'NONE');
      setScopeType(initial.scopeType ?? '__unassigned__');
      setScopeValue(initial.scopeValue ?? '');
      setDeltaValue(initial.deltaValue != null ? String(initial.deltaValue) : '');
      setDeltaUnit(initial.deltaUnit ?? '__unassigned__');
      setDisplayTextOverride(initial.displayTextOverride ?? '');
    } else {
      setMetric(preferredOption(SIGNAL_METRIC, 'ROAS') as SignalMetric);
      setMovement(preferredOption(SIGNAL_MOVEMENT, 'NO_SIGNIFICANT_CHANGE') as SignalMovement);
      setPeriod(preferredOption(SIGNAL_PERIOD, 'LAST_7_DAYS') as SignalPeriod);
      setComparison('NONE');
      setScopeType('__unassigned__');
      setScopeValue('');
      setDeltaValue('');
      setDeltaUnit('__unassigned__');
      setDisplayTextOverride('');
    }
  }, [open, mode, initial]);

  const handleSubmit = async () => {
    setError(null);

    const resolvedScopeType =
      scopeType === '__unassigned__' ? null : (scopeType as SignalScopeType);
    const resolvedScopeValue = scopeValue.trim() || null;
    const deltaValueNum = deltaValue === '' ? null : Number(deltaValue);
    if (deltaValueNum != null && Number.isNaN(deltaValueNum)) {
      setError('Delta value must be a number.');
      return;
    }
    const resolvedDeltaUnit =
      deltaUnit === '__unassigned__' ? null : (deltaUnit as SignalDeltaUnit);

    if (resolvedScopeType === 'CHANNEL' && !resolvedScopeValue) {
      setError('Scope value is required when scope type is CHANNEL.');
      return;
    }
    if ((deltaValueNum == null) !== (resolvedDeltaUnit == null)) {
      setError('Delta value and delta unit must be provided together.');
      return;
    }

    setBusy(true);
    try {
      await onSubmit({
        metric,
        movement,
        period,
        comparison,
        scopeType: resolvedScopeType,
        scopeValue: resolvedScopeValue,
        deltaValue: deltaValueNum,
        deltaUnit: resolvedDeltaUnit,
        displayTextOverride: displayTextOverride.trim() || null,
      });
      onOpenChange(false);
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
          err?.response?.data?.signals ||
          err?.message ||
          'Failed to save signal'
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <BrandDialog
      open={open}
      onOpenChange={onOpenChange}
      title={mode === 'create' ? 'Add signal' : 'Edit signal'}
      subtitle="Signals are structured evidence behind the decision."
      width="max-w-xl"
    >
      {error && (
        <div className="mb-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <Field label="Metric">
          <InlineSelect
            ariaLabel="Metric"
            value={metric}
            onValueChange={(v) => setMetric(v as SignalMetric)}
            options={optionChoices(SIGNAL_METRIC, mode === 'edit' ? initial?.metric : null)}
          />
        </Field>
        <Field label="Movement">
          <InlineSelect
            ariaLabel="Movement"
            value={movement}
            onValueChange={(v) => setMovement(v as SignalMovement)}
            options={optionChoices(SIGNAL_MOVEMENT, mode === 'edit' ? initial?.movement : null)}
          />
        </Field>
        <Field label="Period">
          <InlineSelect
            ariaLabel="Period"
            value={period}
            onValueChange={(v) => setPeriod(v as SignalPeriod)}
            options={optionChoices(SIGNAL_PERIOD, mode === 'edit' ? initial?.period : null)}
          />
        </Field>
        <Field label="Comparison">
          <InlineSelect
            ariaLabel="Comparison"
            value={comparison}
            onValueChange={(v) => setComparison(v as SignalComparison)}
            options={COMPARISON_OPTIONS}
          />
        </Field>
        <Field label="Scope type">
          <InlineSelect
            ariaLabel="Scope type"
            value={scopeType}
            onValueChange={setScopeType}
            options={SCOPE_TYPE_OPTIONS}
          />
        </Field>
        <Field label="Scope value">
          <input
            type="text"
            value={scopeValue}
            onChange={(e) => setScopeValue(e.target.value)}
            placeholder="e.g. Summer promo"
            className="h-9 w-full rounded-md border border-gray-200 bg-white px-3 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </Field>
        <Field label="Delta value">
          <input
            type="number"
            step="0.01"
            value={deltaValue}
            onChange={(e) => setDeltaValue(e.target.value)}
            placeholder="0"
            className="h-9 w-full rounded-md border border-gray-200 bg-white px-3 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </Field>
        <Field label="Delta unit">
          <InlineSelect
            ariaLabel="Delta unit"
            value={deltaUnit}
            onValueChange={setDeltaUnit}
            options={DELTA_UNIT_OPTIONS}
          />
        </Field>
      </div>

      <div className="mt-3">
        <Field label="Display text override (optional)">
          <input
            type="text"
            value={displayTextOverride}
            onChange={(e) => setDisplayTextOverride(e.target.value)}
            placeholder="Leave empty to auto-generate"
            className="h-9 w-full rounded-md border border-gray-200 bg-white px-3 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </Field>
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
          onClick={handleSubmit}
          disabled={busy}
          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-4 text-sm font-medium text-white shadow-sm transition hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {mode === 'create' ? 'Add signal' : 'Save changes'}
        </button>
      </div>
    </BrandDialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-[11px] font-medium uppercase tracking-wide text-gray-500">
        {label}
      </label>
      <div className="mt-1">{children}</div>
    </div>
  );
}
