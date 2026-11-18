'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { GitBranch, FileEdit, Bot } from 'lucide-react';
import type { PendingDecisionDisplay } from '@/types/overview';
import { DECISION_RISK } from '@/lib/decisions/decisionConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle } from '@/lib/orgCustomization/optionConfig';
import type { DecisionRiskLevel } from '@/types/decision';

interface DecisionsCardProps {
  pending: PendingDecisionDisplay[];
  drafts: PendingDecisionDisplay[];
}

const riskStyles: Record<DecisionRiskLevel, { bg: string; text: string }> = {
  HIGH: { bg: 'bg-red-50', text: 'text-red-700' },
  MEDIUM: { bg: 'bg-amber-50', text: 'text-amber-700' },
  LOW: { bg: 'bg-emerald-50', text: 'text-emerald-700' },
};

function formatRelative(iso?: string | null): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const hours = Math.floor(diff / 3_600_000);
  if (hours < 1) return 'Just now';
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function DecisionsCard({ pending, drafts }: DecisionsCardProps) {
  useOptionConfigVersion();
  const top3 = pending.slice(0, 3);

  return (
    <Card
      data-overview-card="decisions"
      className="border-[0.5px] border-gray-200 bg-white shadow-none"
    >
      <CardHeader className="pb-2 px-4 pt-4">
        <div className="flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-gray-400" />
          <CardTitle className="text-sm font-medium text-gray-900">Decisions</CardTitle>
          <span className="ml-auto text-xs text-gray-400">
            {pending.length} awaiting approval
          </span>
        </div>
      </CardHeader>

      <CardContent className="px-4 pb-4 space-y-3">
        <div className="space-y-1">
          {top3.length === 0 && (
            <p className="text-xs text-gray-400 py-2">No pending approvals.</p>
          )}
          {top3.map((d) => {
            const risk = d.riskLevel ? riskStyles[d.riskLevel] : null;
            return (
              <button
                key={d.id}
                className="w-full flex items-start gap-2 py-2 px-1 rounded-md text-left hover:bg-gray-50 transition-colors border-b border-gray-50 last:border-0"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    {risk && (
                      <span
                        className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${risk.bg} ${risk.text}`}
                        style={optionColorStyle(DECISION_RISK.colors[d.riskLevel!])}
                      >
                        {DECISION_RISK.labels[d.riskLevel!]} risk
                      </span>
                    )}
                    {d.createdByAgent && (
                      <span className="inline-flex items-center gap-1 text-[10px] text-[#3CCED7] font-medium">
                        <Bot className="w-3 h-3" />
                        Agent
                      </span>
                    )}
                  </div>
                  <p className="text-[12px] text-gray-800 leading-snug line-clamp-2">
                    {d.title ?? 'Untitled decision'}
                  </p>
                  <div className="flex items-center gap-2 mt-1 text-[10px] text-gray-400">
                    {d.authorName && <span>{d.authorName}</span>}
                    {d.authorName && d.updatedAt && <span>·</span>}
                    {d.updatedAt && <span>{formatRelative(d.updatedAt)}</span>}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-2 pt-2 border-t border-gray-100">
          <FileEdit className="w-3.5 h-3.5 text-gray-400" />
          <span className="text-[12px] text-gray-600">
            <span className="font-medium text-gray-900">{drafts.length}</span> of your drafts
          </span>
          <button className="ml-auto text-[11px] text-[#3CCED7] font-medium hover:underline">
            View all →
          </button>
        </div>
      </CardContent>
    </Card>
  );
}
