'use client';

import { DECISION_RISK } from '@/lib/decisions/decisionConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle } from '@/lib/orgCustomization/optionConfig';
import type { DecisionRiskLevel } from '@/types/decision';

const RISK_TOKEN: Record<DecisionRiskLevel, { bg: string; text: string }> = {
  LOW: { bg: 'bg-sky-50', text: 'text-sky-700' },
  MEDIUM: { bg: 'bg-amber-50', text: 'text-amber-700' },
  HIGH: { bg: 'bg-rose-50', text: 'text-rose-700' },
};

export default function DecisionRiskPill({ risk }: { risk?: DecisionRiskLevel | null }) {
  useOptionConfigVersion();
  if (!risk) {
    return <span className="text-gray-400">—</span>;
  }
  const token = RISK_TOKEN[risk];
  if (!token) {
    return <span className="text-gray-400">—</span>;
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${token.bg} ${token.text}`}
      style={optionColorStyle(DECISION_RISK.colors[risk])}
    >
      {DECISION_RISK.labels[risk]}
    </span>
  );
}
