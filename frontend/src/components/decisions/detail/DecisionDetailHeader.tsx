'use client';

import Link from 'next/link';
import { useBuildUrl } from '@/lib/buildUrl';
import { useEffect, useId, useRef, useState } from 'react';
import { ArrowLeft, Sparkles } from 'lucide-react';
import DecisionStatusPill from '@/components/decisions/DecisionStatusPill';
import DecisionRiskPill from '@/components/decisions/DecisionRiskPill';
import TextLimitHint from '@/components/decisions/detail/TextLimitHint';
import { isComposingKey, useLimitedText } from '@/components/decisions/detail/hooks/useLimitedText';
import { COUNTER_THRESHOLDS, DECISION_LIMITS } from '@/components/decisions/decisionValidation';
import type { DecisionRiskLevel, DecisionStatus } from '@/types/decision';
import type { KeyboardEvent, ReactNode } from 'react';

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
  error?: string | null;
  onErrorDismiss?: () => void;
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
  error,
  onErrorDismiss,
  actionBar,
}: Props) {
  const [local, setLocal] = useState(title ?? '');
  const [saving, setSaving] = useState(false);
  const [focused, setFocused] = useState(false);
  const skipNextSaveRef = useRef(false);
  const hintId = useId();

  const text = useLimitedText({
    value: local,
    onChange: (next) => {
      setLocal(next);
      if (error) onErrorDismiss?.();
    },
    max: DECISION_LIMITS.title,
    counterFrom: COUNTER_THRESHOLDS.title,
    singleLine: true,
  });

  useEffect(() => {
    setLocal(title ?? '');
  }, [title]);

  const buildUrl = useBuildUrl();
  const backHref = buildUrl('/decisions');

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isComposingKey(e)) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      e.currentTarget.blur();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      skipNextSaveRef.current = true;
      setLocal(title ?? '');
      e.currentTarget.blur();
    }
  };

  const handleBlur = async () => {
    setFocused(false);
    text.clearNotice();
    if (skipNextSaveRef.current) {
      skipNextSaveRef.current = false;
      return;
    }
    const normalized = local.trim();
    if (!editable || normalized === (title ?? '').trim()) return;
    setSaving(true);
    try {
      await onTitleSave(normalized);
    } catch {
      // patchDraft already showed a toast; keep the text for a retry.
    } finally {
      setSaving(false);
    }
  };

  const showHint =
    editable && (!!error || (focused && (text.length.showCounter || text.message != null)));

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
          <>
            <textarea
              {...text.textareaProps}
              onKeyDown={handleKeyDown}
              onFocus={() => setFocused(true)}
              onBlur={handleBlur}
              placeholder="Untitled decision"
              aria-label="Decision title"
              aria-describedby={showHint ? hintId : undefined}
              aria-invalid={error ? true : undefined}
              className={`block w-full resize-none break-words border-0 border-b-2 bg-transparent py-1 text-xl font-semibold leading-snug text-gray-900 placeholder:text-gray-300 outline-none transition sm:text-[22px] ${
                error ? 'border-rose-400' : 'border-transparent focus:border-[#3CCED7]'
              }`}
            />
            {showHint && (
              <TextLimitHint id={hintId} length={text.length} message={text.message} error={error} />
            )}
          </>
        ) : (
          <h1 className="break-words text-xl font-semibold text-gray-900 sm:text-[22px]">
            {title?.trim() || <span className="text-gray-300">Untitled decision</span>}
          </h1>
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
