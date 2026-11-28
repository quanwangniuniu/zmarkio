'use client';

import { useEffect, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import InlineSelect from '@/components/tasks/detail/InlineSelect';
import { DECISION_RISK } from '@/lib/decisions/decisionConfig';
import { FIELD_MESSAGES, isConfidenceOutOfRange } from '@/components/decisions/decisionValidation';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionChoices, optionLabel } from '@/lib/orgCustomization/optionConfig';
import type { DecisionRiskLevel, DecisionStatus } from '@/types/decision';
import { resolveUserName, type ProjectMember } from './hooks/useProjectRole';

interface Props {
  status: DecisionStatus | null;
  riskLevel: DecisionRiskLevel | null;
  confidenceScore: number | null;
  plannedDecisionDate: string | null;
  committedAt?: string | null;
  approvedAt?: string | null;
  authorId?: number | null;
  approvedById?: number | null;
  members?: ProjectMember[];
  editable: boolean;
  errors?: {
    risk?: string;
    confidence?: string;
  };
  onRiskChange?: (next: DecisionRiskLevel | null) => void | Promise<void>;
  onConfidenceChange?: (next: number | null) => void | Promise<void>;
  onPlannedDateChange?: (next: string | null) => void | Promise<void>;
}

const UNASSIGNED = '__unassigned__';
// Above the full-screen decision map (z-200).
const MENU_ABOVE_MAP = 'z-[1000]';

const CONFIDENCE_OPTIONS = [
  { value: UNASSIGNED, label: 'Unassigned' },
  ...['1', '2', '3', '4', '5'].map((n) => ({ value: n, label: n })),
];

const LABEL =
  'text-[11px] font-medium uppercase tracking-wide text-gray-500';
const VALUE = 'text-sm text-gray-900';

function formatDate(value?: string | null): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatDateInput(value?: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toISOString().slice(0, 10);
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(72px,88px)_minmax(0,1fr)] items-center gap-3 py-2">
      <span className={LABEL}>{label}</span>
      <div className={`${VALUE} min-w-0`}>{children}</div>
    </div>
  );
}

export default function DecisionPropertiesAside({
  riskLevel,
  confidenceScore,
  plannedDecisionDate,
  committedAt,
  approvedAt,
  authorId,
  approvedById,
  members,
  editable,
  errors,
  onRiskChange,
  onConfidenceChange,
  onPlannedDateChange,
}: Props) {
  useOptionConfigVersion();
  const [plannedLocal, setPlannedLocal] = useState(formatDateInput(plannedDecisionDate));

  useEffect(() => {
    setPlannedLocal(formatDateInput(plannedDecisionDate));
  }, [plannedDecisionDate]);

  const confidenceOutOfRange = isConfidenceOutOfRange(confidenceScore);
  const confidenceError =
    errors?.confidence ||
    (confidenceOutOfRange ? FIELD_MESSAGES.confidenceOutOfRange(confidenceScore) : null);

  const handleConfidenceChange = async (v: string) => {
    try {
      await onConfidenceChange?.(v === UNASSIGNED ? null : Number(v));
    } catch {
      // patchDraft already showed a toast.
    }
  };

  const commitPlanned = async () => {
    const next = plannedLocal ? `${plannedLocal}T00:00:00Z` : null;
    await onPlannedDateChange?.(next);
  };

  const handleRiskChange = async (v: string) => {
    try {
      await onRiskChange?.(v === UNASSIGNED ? null : (v as DecisionRiskLevel));
    } catch {
      // patchDraft already showed a toast.
    }
  };

  return (
    <section className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-100 sm:rounded-xl">
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
        Properties
      </h3>

      <Row label="Risk">
        {editable ? (
          <div>
            <div id="decision-field-risk" className="rounded-md transition">
              <InlineSelect
                ariaLabel="Risk level"
                value={riskLevel ?? UNASSIGNED}
                onValueChange={handleRiskChange}
                menuZIndexClassName={MENU_ABOVE_MAP}
                options={[{ value: UNASSIGNED, label: 'Unassigned' }, ...optionChoices(DECISION_RISK)]}
              />
            </div>
            {errors?.risk && (
              <div className="mt-1 text-[11px] text-rose-600">{errors.risk}</div>
            )}
          </div>
        ) : (
          <span>{riskLevel ? optionLabel(DECISION_RISK, riskLevel) : <span className="text-gray-400">—</span>}</span>
        )}
      </Row>

      <Row label="Confidence">
        {editable ? (
          <div>
            <div id="decision-field-confidence" className="rounded-md transition">
              <InlineSelect
                ariaLabel="Confidence score"
                value={
                  confidenceOutOfRange ? '' : confidenceScore != null ? String(confidenceScore) : UNASSIGNED
                }
                onValueChange={handleConfidenceChange}
                menuZIndexClassName={MENU_ABOVE_MAP}
                options={CONFIDENCE_OPTIONS}
                placeholder="Choose 1–5"
                invalid={!!confidenceError}
                ariaDescribedBy={confidenceError ? 'decision-field-confidence-error' : undefined}
              />
            </div>
            {confidenceError && (
              <p
                id="decision-field-confidence-error"
                className="mt-1 flex items-start gap-1 text-[11px] text-rose-600"
              >
                <AlertCircle aria-hidden="true" className="mt-px h-3 w-3 shrink-0" />
                {confidenceError}
              </p>
            )}
          </div>
        ) : (
          <span>
            {confidenceScore ?? <span className="text-gray-400">—</span>}
          </span>
        )}
      </Row>

      <Row label="Owner">
        {authorId != null ? (
          <span>{resolveUserName(members, authorId)}</span>
        ) : (
          <span className="text-gray-400">—</span>
        )}
      </Row>

      {approvedById != null && (
        <Row label="Approver">
          <span>{resolveUserName(members, approvedById)}</span>
        </Row>
      )}

      <div className="my-2 border-t border-gray-100" />

      <Row label="Planned">
        {editable ? (
          <input
            type="date"
            value={plannedLocal}
            onChange={(e) => setPlannedLocal(e.target.value)}
            onBlur={commitPlanned}
            className="w-full rounded-md border border-gray-200 bg-white px-2 py-1 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        ) : (
          <span>{formatDate(plannedDecisionDate)}</span>
        )}
      </Row>

      <Row label="Committed">
        <span>{formatDate(committedAt)}</span>
      </Row>

      <Row label="Approved">
        <span>{formatDate(approvedAt)}</span>
      </Row>
    </section>
  );
}
