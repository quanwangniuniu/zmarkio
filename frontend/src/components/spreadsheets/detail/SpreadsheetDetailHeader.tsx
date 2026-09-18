'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, ChevronDown, Loader2, Pencil, Bot, Wand2, Sparkles, TableProperties } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { Id } from '@/types/common';
import SpreadsheetBreadcrumb from './SpreadsheetBreadcrumb';

interface Props {
  projectId: Id | null;
  projectName?: string | null;
  spreadsheetName: string;
  canRename?: boolean;
  saving?: boolean;
  onRename?: (newName: string) => Promise<void> | void;
  loading?: boolean;
  /** Optional presence avatars (collab). */
  presenceSlot?: ReactNode;
  /** Back-navigation override passed through to SpreadsheetBreadcrumb. */
  backHref?: string;
  /** Called when the user clicks "Analyze with AI". */
  onAnalyzeWithAgent?: () => void;
  analyzeDisabled?: boolean;
  /** Called when the user clicks "Pattern Agent" (e.g. to expand the panel). */
  onOpenPatternAgent?: () => void;
  patternAgentDisabled?: boolean;
  /** Called when the user clicks "Generate Pattern with AI". */
  onOpenPatternGenerationAgent?: () => void;
  patternGenerationAgentDisabled?: boolean;
  /** Called when the user clicks "Generate Pivot Table with AI". */
  onOpenPivotGenerationAgent?: () => void;
  pivotGenerationAgentDisabled?: boolean;
}

export default function SpreadsheetDetailHeader({
  projectId,
  projectName,
  spreadsheetName,
  canRename = true,
  saving = false,
  onRename,
  loading = false,
  presenceSlot,
  backHref,
  onAnalyzeWithAgent,
  analyzeDisabled = false,
  onOpenPatternAgent,
  patternAgentDisabled = false,
  onOpenPatternGenerationAgent,
  patternGenerationAgentDisabled = false,
  onOpenPivotGenerationAgent,
  pivotGenerationAgentDisabled = false,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(spreadsheetName);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!dropdownOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [dropdownOpen]);

  useEffect(() => {
    if (!editing) setDraft(spreadsheetName);
  }, [spreadsheetName, editing]);

  const commit = async () => {
    const trimmed = draft.trim();
    if (!trimmed || trimmed === spreadsheetName) {
      setEditing(false);
      setDraft(spreadsheetName);
      return;
    }
    if (onRename) {
      try {
        await onRename(trimmed);
      } finally {
        setEditing(false);
      }
    } else {
      setEditing(false);
    }
  };

  return (
    <header className="space-y-3 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
      <SpreadsheetBreadcrumb
        projectId={projectId}
        projectName={projectName}
        spreadsheetName={spreadsheetName}
        backHref={backHref}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Title */}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {loading ? (
            <div className="flex w-full max-w-xl items-center gap-2">
              <Skeleton className="h-9 w-full max-w-md rounded-md" />
              <Skeleton className="h-8 w-8 rounded-full" />
            </div>
          ) : editing ? (
            <div className="flex w-full max-w-xl items-center gap-2">
              <input
                autoFocus
                type="text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void commit();
                  if (e.key === 'Escape') {
                    setEditing(false);
                    setDraft(spreadsheetName);
                  }
                }}
                className="flex-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-lg font-semibold text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
                maxLength={200}
              />
              <button
                type="button"
                onClick={() => void commit()}
                disabled={saving}
                className="inline-flex h-8 items-center gap-1 rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 text-xs font-semibold text-white shadow-sm transition hover:opacity-95 disabled:opacity-60"
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                Save
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setDraft(spreadsheetName);
                }}
                className="inline-flex h-8 items-center rounded-md px-2.5 text-xs font-medium text-gray-600 transition hover:bg-gray-100"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <h1 className="line-clamp-1 text-2xl font-semibold text-gray-900">
                {spreadsheetName || 'Untitled spreadsheet'}
              </h1>
              {canRename && onRename && (
                <button
                  type="button"
                  aria-label="Rename spreadsheet"
                  onClick={() => setEditing(true)}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
                >
                  <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              )}
            </div>
          )}
        </div>

        {/* Workflow chooser */}
        {(onAnalyzeWithAgent || onOpenPatternAgent || onOpenPatternGenerationAgent || onOpenPivotGenerationAgent) && !loading && (
          <div ref={dropdownRef} className="relative shrink-0">
            <button
              type="button"
              onClick={() => setDropdownOpen((o) => !o)}
              disabled={analyzeDisabled && patternAgentDisabled && patternGenerationAgentDisabled && pivotGenerationAgentDisabled}
              className="inline-flex h-9 items-center gap-1.5 rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3.5 text-sm font-semibold text-white shadow-sm transition hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Bot className="h-4 w-4" strokeWidth={2.3} aria-hidden="true" />
              AI Workflows
              <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-150 ${dropdownOpen ? 'rotate-180' : ''}`} aria-hidden="true" />
            </button>

            {dropdownOpen && (
              <div className="absolute right-0 top-full z-50 mt-1.5 w-72 overflow-hidden rounded-xl border border-gray-100 bg-white shadow-xl ring-1 ring-black/5">
                <div className="p-1.5">
                  <p className="px-2.5 pb-1.5 pt-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                    Choose a workflow
                  </p>
                  {onAnalyzeWithAgent && (
                    <button
                      type="button"
                      disabled={analyzeDisabled}
                      onClick={() => { onAnalyzeWithAgent(); setDropdownOpen(false); }}
                      className="flex w-full items-start gap-3 rounded-lg p-3 text-left transition hover:bg-[#3CCED7]/8 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-[#3CCED7] to-[#A6E661]">
                        <Bot className="h-4 w-4 text-white" strokeWidth={2.3} />
                      </span>
                      <span>
                        <span className="block text-sm font-semibold text-gray-900">Analyze with AI</span>
                        <span className="block text-xs text-gray-500">Ask questions, get insights, and explore the data.</span>
                      </span>
                    </button>
                  )}
                  {onOpenPatternAgent && (
                    <button
                      type="button"
                      disabled={patternAgentDisabled}
                      onClick={() => { onOpenPatternAgent(); setDropdownOpen(false); }}
                      className="flex w-full items-start gap-3 rounded-lg p-3 text-left transition hover:bg-purple-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-purple-100">
                        <Wand2 className="h-4 w-4 text-purple-600" />
                      </span>
                      <span>
                        <span className="block text-sm font-semibold text-gray-900">Pattern Agent</span>
                        <span className="block text-xs text-gray-500">Record grid edits as reusable steps and replay them as automated patterns on any sheet.</span>
                      </span>
                    </button>
                  )}
                  {onOpenPatternGenerationAgent && (
                    <button
                      type="button"
                      disabled={patternGenerationAgentDisabled}
                      onClick={() => { onOpenPatternGenerationAgent(); setDropdownOpen(false); }}
                      className="flex w-full items-start gap-3 rounded-lg p-3 text-left transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-emerald-100">
                        <Sparkles className="h-4 w-4 text-emerald-600" />
                      </span>
                      <span>
                        <span className="block text-sm font-semibold text-gray-900">Generate Pattern with AI</span>
                        <span className="block text-xs text-gray-500">Describe want to do — AI will generate the pattern steps for you.</span>
                      </span>
                    </button>
                  )}
                  {onOpenPivotGenerationAgent && (
                    <button
                      type="button"
                      disabled={pivotGenerationAgentDisabled}
                      onClick={() => { onOpenPivotGenerationAgent(); setDropdownOpen(false); }}
                      className="flex w-full items-start gap-3 rounded-lg p-3 text-left transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-100">
                        <TableProperties className="h-4 w-4 text-indigo-600" />
                      </span>
                      <span>
                        <span className="block text-sm font-semibold text-gray-900">Generate Pivot Table with AI</span>
                        <span className="block text-xs text-gray-500">Describe the pivot table you want — AI will build it.</span>
                      </span>
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
        {presenceSlot ? <div className="ml-auto shrink-0">{presenceSlot}</div> : null}
      </div>
    </header>
  );
}
