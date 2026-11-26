'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { DecisionAPI, DecisionSignalPayload } from '@/lib/api/decisionApi';
import SignalBuilderModal, { buildSignalPreviewText } from '@/components/decisions/SignalBuilderModal';
import type {
  DecisionSignal,
  SignalComparison,
  SignalDeltaUnit,
  SignalMetric,
  SignalMovement,
  SignalPeriod,
  SignalScopeType,
} from '@/types/decision';

interface SignalsPanelProps {
  decisionId: number;
  projectId?: number | null;
  mode?: 'edit' | 'readOnly';
}

const metricLabels: Record<SignalMetric, string> = {
  ROAS: 'ROAS',
  CPA: 'CPA',
  CONVERSION_RATE: 'Conversion rate',
  REVENUE: 'Revenue',
  PURCHASES: 'Purchases',
  CTR: 'CTR',
  CLICKS: 'Clicks',
  IMPRESSIONS: 'Impressions',
  CPC: 'CPC',
  CPM: 'CPM',
  AD_SPEND: 'Ad spend',
  AOV: 'AOV',
};

const movementLabels: Record<SignalMovement, string> = {
  SHARP_INCREASE: 'a sharp increase',
  MODERATE_INCREASE: 'a moderate increase',
  SLIGHT_INCREASE: 'a slight increase',
  NO_SIGNIFICANT_CHANGE: 'no significant change',
  SLIGHT_DECREASE: 'a slight decrease',
  MODERATE_DECREASE: 'a moderate decrease',
  SHARP_DECREASE: 'a sharp decrease',
  VOLATILE: 'volatile movement',
  UNEXPECTED_SPIKE: 'an unexpected spike',
  UNEXPECTED_DROP: 'an unexpected drop',
};

const periodLabels: Record<SignalPeriod, string> = {
  LAST_24_HOURS: 'the last 24 hours',
  LAST_3_DAYS: 'the last 3 days',
  LAST_7_DAYS: 'the last 7 days',
  LAST_14_DAYS: 'the last 14 days',
  LAST_30_DAYS: 'the last 30 days',
};

const scopeLabels: Record<SignalScopeType, string> = {
  CAMPAIGN: 'Campaign',
  AD_SET: 'Ad set',
  AD: 'Ad',
  CHANNEL: 'Channel',
  AUDIENCE: 'Audience',
  REGION: 'Region',
};

const SignalsPanel = ({ decisionId, projectId, mode = 'edit' }: SignalsPanelProps) => {
  const [signals, setSignals] = useState<DecisionSignal[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const [metric, setMetric] = useState<SignalMetric | ''>('');
  const [movement, setMovement] = useState<SignalMovement | ''>('');
  const [period, setPeriod] = useState<SignalPeriod | ''>('');
  const [comparison, setComparison] = useState<SignalComparison>('NONE');
  const [scopeType, setScopeType] = useState<SignalScopeType | ''>('');
  const [scopeValue, setScopeValue] = useState('');
  const [deltaValue, setDeltaValue] = useState<string>('');
  const [deltaUnit, setDeltaUnit] = useState<SignalDeltaUnit | ''>('');
  const [overrideText, setOverrideText] = useState('');
  const [hasOverride, setHasOverride] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [descriptionEditable, setDescriptionEditable] = useState(false);
  const [baseDescriptionText, setBaseDescriptionText] = useState('');
  const [movementGroup, setMovementGroup] = useState<
    'INCREASE' | 'DECREASE' | 'NO_CHANGE' | 'VOLATILE' | 'UNEXPECTED' | ''
  >('');
  const [movementDetail, setMovementDetail] = useState<
    'SLIGHT' | 'MODERATE' | 'SHARP' | 'SPIKE' | 'DROP' | ''
  >('');

  const previewPayload = useMemo(
    () => ({
      metric: metric || undefined,
      movement: movement || undefined,
      period: period || undefined,
      comparison,
      scopeType: scopeType || undefined,
      scopeValue: scopeValue || undefined,
      deltaValue: deltaValue ? Number(deltaValue) : undefined,
      deltaUnit: deltaUnit || undefined,
    }),
    [metric, movement, period, comparison, scopeType, scopeValue, deltaValue, deltaUnit]
  );

  const descriptionText = useMemo(() => buildSignalPreviewText(previewPayload), [previewPayload]);

  useEffect(() => {
    if (!descriptionEditable) {
      if (editingId) {
        setOverrideText(baseDescriptionText);
      } else {
        setOverrideText(descriptionText);
      }
    }
  }, [descriptionText, descriptionEditable, editingId, baseDescriptionText]);

  const loadSignals = useCallback(async () => {
    if (!decisionId) return;
    setLoading(true);
    try {
      const response = await DecisionAPI.listSignals(decisionId, projectId);
      setSignals(response.items || []);
    } catch (error) {
      console.error('Failed to load signals:', error);
      setErrorMessage('Failed to load signals.');
    } finally {
      setLoading(false);
    }
  }, [decisionId, projectId]);

  useEffect(() => {
    loadSignals();
  }, [loadSignals]);

  const resetBuilder = () => {
    setMetric('');
    setMovement('');
    setPeriod('');
    setComparison('NONE');
    setScopeType('');
    setScopeValue('');
    setDeltaValue('');
    setDeltaUnit('');
    setOverrideText('');
    setHasOverride(false);
    setDescriptionEditable(false);
    setEditingId(null);
    setErrorMessage(null);
    setMovementGroup('');
    setMovementDetail('');
    setBaseDescriptionText(descriptionText);
  };

  useEffect(() => {
    setErrorMessage(null);
  }, [metric, movement, period, comparison, scopeType, scopeValue, deltaValue, deltaUnit]);

  const handleSubmit = async () => {
    if (!metric || !movement || !period) return;
    if (scopeType === 'CHANNEL' && !scopeValue.trim()) {
      setErrorMessage('Scope value is required when scope type is CHANNEL.');
      return;
    }
    if ((deltaValue && !deltaUnit) || (!deltaValue && deltaUnit)) {
      setErrorMessage('Delta value and unit must be provided together.');
      return;
    }
    setErrorMessage(null);
    const emptyValue = editingId ? null : undefined;
    const payload: DecisionSignalPayload = {
      metric: metric as SignalMetric,
      movement: movement as SignalMovement,
      period: period as SignalPeriod,
      comparison,
      scopeType: scopeType || emptyValue,
      scopeValue: scopeValue.trim() || emptyValue,
      deltaValue: deltaValue === '' ? emptyValue : Number(deltaValue),
      deltaUnit: deltaUnit || emptyValue,
      displayTextOverride: overrideText.trim() || emptyValue,
    };

    try {
      if (editingId) {
        setSavingId(editingId);
        const updated = await DecisionAPI.updateSignal(
          decisionId,
          editingId,
          payload,
          projectId
        );
        setSignals((prev) =>
          prev.map((signal) => (signal.id === updated.id ? updated : signal))
        );
        toast.success('Signal updated.');
      } else {
        setSavingId(-1);
        const created = await DecisionAPI.createSignal(decisionId, payload, projectId);
        setSignals((prev) => [created, ...prev]);
        toast.success('Signal added.');
      }
      resetBuilder();
      setModalOpen(false);
      } catch (error: any) {
        const response = error?.response;
        if (response?.status === 400) {
          const data = response?.data || {};
          const detailMessage =
            data?.signals ||
            data?.detail ||
            data?.scopeValue ||
            data?.deltaValue ||
            data?.message ||
            'Validation error. Check required fields.';
          setErrorMessage(detailMessage);
        } else if (response?.status === 403) {
          setErrorMessage('Only creator can edit signals.');
        } else if (response?.status === 409) {
          setErrorMessage('Signals can only be edited in DRAFT.');
        } else {
          setErrorMessage('Failed to save signal.');
        }
      } finally {
        setSavingId(null);
      }
  };

  const handleEdit = (signal: DecisionSignal) => {
    setEditingId(signal.id);
    setMetric(signal.metric);
    setMovement(signal.movement);
    setPeriod(signal.period);
    setComparison(signal.comparison || 'NONE');
    setScopeType(signal.scopeType || '');
    setScopeValue(signal.scopeValue || '');
    setDeltaValue(signal.deltaValue !== null && signal.deltaValue !== undefined ? String(signal.deltaValue) : '');
    setDeltaUnit(signal.deltaUnit || '');
    if (signal.displayTextOverride) {
      setOverrideText(signal.displayTextOverride);
      setHasOverride(true);
    } else {
      setOverrideText(signal.displayText);
      setHasOverride(false);
    }
    setDescriptionEditable(false);
    setBaseDescriptionText(signal.displayText);
    if (
      signal.movement === 'SLIGHT_INCREASE' ||
      signal.movement === 'MODERATE_INCREASE' ||
      signal.movement === 'SHARP_INCREASE'
    ) {
      setMovementGroup('INCREASE');
      setMovementDetail(
        signal.movement === 'SLIGHT_INCREASE'
          ? 'SLIGHT'
          : signal.movement === 'MODERATE_INCREASE'
          ? 'MODERATE'
          : 'SHARP'
      );
    } else if (
      signal.movement === 'SLIGHT_DECREASE' ||
      signal.movement === 'MODERATE_DECREASE' ||
      signal.movement === 'SHARP_DECREASE'
    ) {
      setMovementGroup('DECREASE');
      setMovementDetail(
        signal.movement === 'SLIGHT_DECREASE'
          ? 'SLIGHT'
          : signal.movement === 'MODERATE_DECREASE'
          ? 'MODERATE'
          : 'SHARP'
      );
    } else if (signal.movement === 'UNEXPECTED_SPIKE') {
      setMovementGroup('UNEXPECTED');
      setMovementDetail('SPIKE');
    } else if (signal.movement === 'UNEXPECTED_DROP') {
      setMovementGroup('UNEXPECTED');
      setMovementDetail('DROP');
    } else if (signal.movement === 'NO_SIGNIFICANT_CHANGE') {
      setMovementGroup('NO_CHANGE');
      setMovementDetail('');
    } else if (signal.movement === 'VOLATILE') {
      setMovementGroup('VOLATILE');
      setMovementDetail('');
    } else {
      setMovementGroup('');
      setMovementDetail('');
    }
    setModalOpen(true);
  };

  const handleDelete = async (signalId: number) => {
    try {
      setSavingId(signalId);
      await DecisionAPI.deleteSignal(decisionId, signalId, projectId);
      setSignals((prev) => prev.filter((signal) => signal.id !== signalId));
    } catch (error) {
      setErrorMessage('Failed to delete signal.');
    } finally {
      setSavingId(null);
    }
  };

  const canSubmit =
    !!metric &&
    !!movement &&
    !!period &&
    !(scopeType === 'CHANNEL' && !scopeValue.trim()) &&
    !((deltaValue && !deltaUnit) || (!deltaValue && deltaUnit));
  const signalLimitReached = signals.length >= 15;

  return (
    <div className="h-full overflow-y-auto border-r border-gray-200 bg-white">
      <div className="px-4 py-4">
        <h3 className="text-sm font-semibold text-gray-900">Signals</h3>
        <p className="mt-1 text-xs text-gray-500">
          Structured signals describe the key evidence behind the decision.
        </p>
      </div>

      <div className="space-y-3 px-4 pb-4">
        {loading ? (
          <div className="text-xs text-gray-500">Loading signals...</div>
        ) : signals.length === 0 ? (
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            disabled={mode !== 'edit' || signalLimitReached}
            className={`flex w-full items-center justify-between rounded-lg border border-dashed px-3 py-4 text-left text-sm font-semibold transition ${
              mode === 'edit' && !signalLimitReached
                ? 'border-gray-300 text-gray-700 hover:border-gray-400'
                : 'cursor-not-allowed border-gray-200 text-gray-300'
            }`}
          >
            + Add Signal
          </button>
        ) : (
          <>
            {signals.map((signal) => (
              <div
                key={signal.id}
                className="rounded-lg border border-gray-200 bg-white p-3"
              >
              <div className="text-sm font-medium text-gray-900">
                {signal.displayText}
              </div>
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-gray-500">
                  <span className="rounded-full bg-gray-100 px-2 py-0.5">
                    {metricLabels[signal.metric]}
                  </span>
                  <span className="rounded-full bg-gray-100 px-2 py-0.5">
                    {movementLabels[signal.movement]}
                  </span>
                  <span className="rounded-full bg-gray-100 px-2 py-0.5">
                    {periodLabels[signal.period]}
                  </span>
                  {signal.scopeType ? (
                    <span className="rounded-full bg-gray-100 px-2 py-0.5">
                      {scopeLabels[signal.scopeType]}
                      {signal.scopeValue ? `: ${signal.scopeValue}` : ''}
                    </span>
                  ) : null}
                </div>
                {mode === 'edit' ? (
                  <div className="mt-3 flex items-center gap-2 text-xs">
                    <button
                      type="button"
                      onClick={() => handleEdit(signal)}
                      disabled={savingId !== null}
                      className="rounded-md border border-gray-200 px-2 py-1 text-gray-600 hover:border-gray-300"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(signal.id)}
                      disabled={savingId !== null}
                      className="rounded-md border border-gray-200 px-2 py-1 text-red-500 hover:border-red-200"
                    >
                      {savingId === signal.id ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              disabled={mode !== 'edit' || signalLimitReached}
              className={`flex w-full items-center justify-between rounded-lg border border-dashed px-3 py-4 text-left text-sm font-semibold transition ${
                mode === 'edit' && !signalLimitReached
                  ? 'border-gray-300 text-gray-700 hover:border-gray-400'
                  : 'cursor-not-allowed border-gray-200 text-gray-300'
              }`}
            >
              + Add Signal
            </button>
            {signalLimitReached && mode === 'edit' ? (
              <div className="text-xs text-gray-400">Maximum 15 signals per decision.</div>
            ) : null}
          </>
        )}
      </div>
      <SignalBuilderModal
        isOpen={modalOpen}
        onClose={() => {
          setModalOpen(false);
          if (editingId) {
            resetBuilder();
          }
        }}
        mode={editingId ? 'edit' : 'create'}
        metric={metric}
        movement={movement}
        period={period}
        comparison={comparison}
        scopeType={scopeType}
        scopeValue={scopeValue}
        deltaValue={deltaValue}
        deltaUnit={deltaUnit}
        movementGroup={movementGroup}
        movementDetail={movementDetail}
        overrideText={overrideText}
        showResetAutoText
        descriptionEditable={descriptionEditable}
        descriptionText={overrideText}
        errorMessage={errorMessage}
        canSubmit={canSubmit}
        saving={savingId !== null}
        onMetricChange={setMetric}
        onPeriodChange={setPeriod}
        onComparisonChange={setComparison}
        onScopeTypeChange={setScopeType}
        onScopeValueChange={setScopeValue}
        onDeltaValueChange={setDeltaValue}
        onDeltaUnitChange={setDeltaUnit}
        onMovementGroupChange={(group) => {
          setMovementGroup(group);
          setMovementDetail('');
          if (group === 'NO_CHANGE') {
            setMovement('NO_SIGNIFICANT_CHANGE');
          } else if (group === 'VOLATILE') {
            setMovement('VOLATILE');
          } else {
            setMovement('');
          }
        }}
        onMovementDetailChange={(detail) => {
          setMovementDetail(detail);
          if (movementGroup === 'INCREASE') {
            setMovement(
              detail === 'SLIGHT'
                ? 'SLIGHT_INCREASE'
                : detail === 'MODERATE'
                ? 'MODERATE_INCREASE'
                : 'SHARP_INCREASE'
            );
          } else if (movementGroup === 'DECREASE') {
            setMovement(
              detail === 'SLIGHT'
                ? 'SLIGHT_DECREASE'
                : detail === 'MODERATE'
                ? 'MODERATE_DECREASE'
                : 'SHARP_DECREASE'
            );
          } else if (movementGroup === 'UNEXPECTED') {
            setMovement(detail === 'SPIKE' ? 'UNEXPECTED_SPIKE' : 'UNEXPECTED_DROP');
          }
        }}
        onOverrideTextChange={(value) => {
          setOverrideText(value);
          setDescriptionEditable(true);
        }}
        onClearOverride={() => {
          setOverrideText(descriptionText);
          setHasOverride(false);
          setDescriptionEditable(false);
          setBaseDescriptionText(descriptionText);
        }}
        onDescriptionActivate={() => setDescriptionEditable(true)}
        onSubmit={handleSubmit}
        onCancelEdit={resetBuilder}
      />
    </div>
  );
};

export default SignalsPanel;
