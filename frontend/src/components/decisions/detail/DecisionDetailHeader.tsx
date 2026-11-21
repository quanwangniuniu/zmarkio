'use client';

import Link from 'next/link';
import { useBuildUrl } from '@/lib/buildUrl';
import { useEffect, useState } from 'react';
import { ArrowLeft, Sparkles } from 'lucide-react';
import DecisionStatusPill from '@/components/decisions/DecisionStatusPill';
import DecisionRiskPill from '@/components/decisions/DecisionRiskPill';
import type { DecisionRiskLevel, DecisionStatus } from '@/types/decision';
import type { ReactNode } from 'react';

interface Props {
  projectId: number | string | null;
  projectName?: string | null;
  projectSeq?: number | null;
  title: string;
  status: DecisionStatus | null;
  riskLevel?: DecisionRiskLevel | null;
  createdByAgent?: boolean;
  editable: boolean;
  onTitleSave: (next: string) => void | Promise<void>;
  titleError?: string;
  actionBar?: ReactNode;
}

export default function DecisionDetailHeader({
  projectId,
  projectName,
  projectSeq,
  title,
  status,
  riskLevel,
  createdByAgent,
  editable,
  onTitleSave,
  titleError,
  actionBar,
}: Props) {
  const [local, setLocal] = useState(title ?? '');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLocal(title ?? '');
  }, [title]);

  const buildUrl = useBuildUrl();
  const backHref = buildUrl('/decisions');

  const handleBlur = async () => {
    const normalized = local.trim();
    if (!editable || (!titleError && normalized === (title ?? '').trim())) return;
    setSaving(true);
    try {
      await onTitleSave(normalized);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-100 sm:rounded-xl">
      <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-3 py-2 sm:px-6">
        <nav className="flex min-w-0 items-center gap-1.5 text-xs text-gray-500">
          <Link
            href={backHref}
            aria-label="Back to decisions"
            title="Back to Decisions"
            className="inline-flex h-6 w-6 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-50 hover:text-gray-900"
          >
            <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
          </Link>
          <span className="truncate uppercase tracking-wider">{projectName || 'Project'}</span>
          {projectSeq != null && (
            <>
              <span className="text-gray-300">/</span>
              <span className="font-medium text-gray-700">#{projectSeq}</span>
            </>
          )}
        </nav>
        {saving && <span className="text-[11px] text-gray-400">Saving…</span>}
      </div>

      <div className="px-3 py-4 sm:px-6 sm:py-5">
        {editable ? (
          <input
            type="text"
            value={local}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={handleBlur}
            placeholder="Untitled decision"
            aria-label="Decision title"
            aria-invalid={Boolean(titleError)}
            aria-describedby={titleError ? 'decision-title-error' : undefined}
            className="w-full border-0 border-b-2 border-transparent bg-transparent py-1 text-xl font-semibold text-gray-900 placeholder:text-gray-300 outline-none transition focus:border-[#3CCED7] sm:text-[22px]"
          />
        ) : (
          <h1 className="break-words text-xl font-semibold text-gray-900 sm:text-[22px]">
            {title?.trim() || <span className="text-gray-300">Untitled decision</span>}
          </h1>
        )}
        {editable && titleError && (
          <p id="decision-title-error" role="alert" className="mt-1 text-xs text-rose-600">
            {titleError}
          </p>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2 text-[12px] text-gray-500">
          <DecisionStatusPill status={status ?? undefined} />
          {riskLevel && <DecisionRiskPill risk={riskLevel} />}
          {createdByAgent && (
            <span className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">
              <Sparkles aria-hidden="true" className="h-3 w-3" />
              AI drafted
            </span>
          )}
        </div>
      </div>

      {actionBar && (
        <div className="border-t border-gray-100 bg-gray-50/40 px-3 py-3 sm:px-6">{actionBar}</div>
      )}
    </section>
  );
}
