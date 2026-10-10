'use client';

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Download,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import type { RollupProjectResult } from '@/lib/api/dashboardApi';
import { exportMatrixToXLSX } from '@/components/spreadsheets/spreadsheetImportExport';

const PAGE_SIZE = 5;

// Fields always fetched (primary + detail)
const ALL_FIELDS = [
  'task_total', 'task_done', 'task_overdue', 'task_blocked',
  'task_under_review', 'task_rejected', 'task_due_soon',
  'task_created_7d', 'task_completed_7d',
  'decision_total', 'decision_pending', 'decision_high_risk',
  'spreadsheet_total', 'campaign_active', 'meeting_upcoming',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(numerator: number, denominator: number): number {
  if (denominator === 0) return 0;
  return Math.round((numerator / denominator) * 100);
}

function val(row: RollupProjectResult, key: string): number {
  const v = row[key];
  return typeof v === 'number' ? v : 0;
}

// ---------------------------------------------------------------------------
// ProgressBar — mini inline bar
// ---------------------------------------------------------------------------

function ProgressBar({ value, color = '#3CCED7' }: { value: number; color?: string }) {
  return (
    <div className="h-[3px] w-full rounded-full bg-gray-100 mt-1.5 overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${Math.min(value, 100)}%`, background: color }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// SummaryCell — one metric card in the main table row
// ---------------------------------------------------------------------------

function SummaryCell({
  value,
  sub,
  valueColor = '#334155',
  bar,
  barColor,
}: {
  value: string | number;
  sub?: string;
  valueColor?: string;
  bar?: number;
  barColor?: string;
}) {
  return (
    <td className="px-3 py-3 align-top min-w-[130px]">
      <p className="text-[22px] font-bold leading-none" style={{ color: valueColor }}>
        {value}
      </p>
      {bar !== undefined && <ProgressBar value={bar} color={barColor} />}
      {sub && (
        <p className="text-[11px] text-gray-400 mt-1 whitespace-nowrap">{sub}</p>
      )}
    </td>
  );
}

// ---------------------------------------------------------------------------
// DetailMetricRow — label + value row inside the detail panel
// ---------------------------------------------------------------------------

function DetailMetricRow({
  label,
  value,
  valueColor = '#334155',
}: {
  label: string;
  value: string | number;
  valueColor?: string;
}) {
  return (
    <div className="flex items-center justify-between py-[7px] border-b border-gray-50 gap-2">
      <span className="text-[12px] text-gray-500 truncate">{label}</span>
      <span
        className="text-[13px] font-semibold tabular-nums flex-shrink-0"
        style={{ color: valueColor }}
      >
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DetailPanel — 3-column module summary for one project row
// ---------------------------------------------------------------------------

function DetailPanel({ row }: { row: RollupProjectResult }) {
  const taskTotal = val(row, 'task_total');
  const taskDone = val(row, 'task_done');
  const completionPct = pct(taskDone, taskTotal);
  const completedLast7d = val(row, 'task_completed_7d');

  return (
    <tr>
      <td colSpan={6} className="px-4 pb-4 pt-1">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 rounded-xl border border-gray-100 bg-gray-50/60 p-4">

          {/* Decisions */}
          <div className="rounded-lg border border-gray-100 bg-white p-3">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-6 h-6 rounded-md flex items-center justify-center bg-teal-50 flex-shrink-0">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#14b8a6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="3" x2="12" y2="20" />
                  <line x1="4" y1="6" x2="20" y2="6" />
                  <line x1="4" y1="6" x2="4" y2="14" />
                  <line x1="20" y1="6" x2="20" y2="14" />
                  <path d="M1 14h6a3 3 0 0 1-6 0z" />
                  <path d="M17 14h6a3 3 0 0 1-6 0z" />
                  <line x1="9" y1="20" x2="15" y2="20" />
                </svg>
              </div>
              <span className="text-[13px] font-semibold text-gray-800">Decisions</span>
            </div>
            <DetailMetricRow label="Active decisions" value={val(row, 'decision_total')} />
            <DetailMetricRow
              label="Awaiting approval"
              value={val(row, 'decision_pending')}
              valueColor={val(row, 'decision_pending') > 0 ? '#f59e0b' : '#334155'}
            />
            <DetailMetricRow
              label="High risk"
              value={val(row, 'decision_high_risk')}
              valueColor={val(row, 'decision_high_risk') > 0 ? '#fb7185' : '#334155'}
            />
          </div>

          {/* Tasks */}
          <div className="rounded-lg border border-gray-100 bg-white p-3">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-6 h-6 rounded-md flex items-center justify-center bg-amber-50 flex-shrink-0">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 11 12 14 22 4" />
                  <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                </svg>
              </div>
              <span className="text-[13px] font-semibold text-gray-800">Tasks</span>
            </div>
            <DetailMetricRow label="Completion rate" value={`${completionPct}%`} valueColor={completionPct >= 50 ? '#34d399' : '#334155'} />
            <DetailMetricRow label="Under review" value={val(row, 'task_under_review')} valueColor={val(row, 'task_under_review') > 0 ? '#3CCED7' : '#334155'} />
            <DetailMetricRow label="Rejected" value={val(row, 'task_rejected')} valueColor={val(row, 'task_rejected') > 0 ? '#f59e0b' : '#334155'} />
            <DetailMetricRow label="Blocked" value={val(row, 'task_blocked')} valueColor={val(row, 'task_blocked') > 0 ? '#fb7185' : '#334155'} />
            <DetailMetricRow label="Due soon (7d)" value={val(row, 'task_due_soon')} valueColor={val(row, 'task_due_soon') > 0 ? '#f59e0b' : '#334155'} />
            <DetailMetricRow label="Created last 7d" value={val(row, 'task_created_7d')} />
            <DetailMetricRow
              label="Completed last 7d"
              value={completedLast7d}
              valueColor={completedLast7d > 0 ? '#34d399' : '#334155'}
            />
          </div>

          {/* Operations */}
          <div className="rounded-lg border border-gray-100 bg-white p-3">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-6 h-6 rounded-md flex items-center justify-center bg-green-50 flex-shrink-0">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="14" width="7" height="7" rx="1" />
                  <rect x="3" y="14" width="7" height="7" rx="1" />
                </svg>
              </div>
              <span className="text-[13px] font-semibold text-gray-800">Operations</span>
            </div>
            <DetailMetricRow label="Active spreadsheets" value={val(row, 'spreadsheet_total')} />
            <DetailMetricRow label="Active campaigns" value={val(row, 'campaign_active')} />
            <DetailMetricRow label="Upcoming meetings" value={val(row, 'meeting_upcoming')} />
          </div>

        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// MultiSelectDropdown — inline multi-select with checkbox list
// ---------------------------------------------------------------------------

interface MultiSelectProps {
  label: string;
  options: { key: string; label: string }[];
  value: string[];
  onChange: (newValue: string[]) => void;
}

function MultiSelectDropdown({ label, options, value, onChange }: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selectedSet = useMemo(() => new Set(value), [value]);
  const allSelected = value.length === options.length;

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggle = (key: string) => {
    if (selectedSet.has(key)) {
      onChange(value.filter((k) => k !== key));
    } else {
      onChange([...value, key]);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-[13px] hover:border-gray-300 transition-colors whitespace-nowrap"
      >
        <span className="text-gray-500">{label}:</span>
        <span className="font-medium text-gray-800">
          {allSelected ? 'All' : `${value.length} selected`}
        </span>
        <ChevronDown
          className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute z-20 top-full mt-1 left-0 min-w-[200px] max-h-64 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg py-1">
          <button
            type="button"
            onClick={() => onChange(allSelected ? [] : options.map((o) => o.key))}
            className="w-full px-3 py-2 text-left text-[11px] font-semibold text-[#3CCED7] hover:bg-gray-50"
          >
            {allSelected ? 'Deselect all' : 'Select all'}
          </button>
          <div className="my-1 h-px bg-gray-100" />
          {options.map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => toggle(opt.key)}
              className="flex items-center gap-2.5 w-full px-3 py-1.5 text-[13px] text-left hover:bg-gray-50 transition-colors"
            >
              <span
                className={`w-3.5 h-3.5 rounded border flex-shrink-0 flex items-center justify-center transition-colors ${
                  selectedSet.has(opt.key)
                    ? 'bg-[#3CCED7] border-[#3CCED7]'
                    : 'border-gray-300'
                }`}
              >
                {selectedSet.has(opt.key) && (
                  <svg
                    viewBox="0 0 10 8"
                    className="w-2.5 h-2 fill-none stroke-white"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M1 4l2.5 2.5L9 1" />
                  </svg>
                )}
              </span>
              <span className="text-gray-700">{opt.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CrossProjectRollupWidget
// ---------------------------------------------------------------------------

interface CrossProjectRollupWidgetProps {
  onSelectProject?: (id: number, name: string) => void;
}

export default function CrossProjectRollupWidget({ onSelectProject }: CrossProjectRollupWidgetProps) {
  const [allData, setAllData] = useState<RollupProjectResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>([]);
  const [windowStart, setWindowStart] = useState(0);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  const hasFetched = useRef(false);

  const doFetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await DashboardAPI.getRollup();
      setAllData(data.results);
      setSelectedProjectIds(data.results.map((r) => String(r.project_id)));
      setWindowStart(0);
    } catch {
      setError('Failed to load comparison data. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (hasFetched.current) return;
    hasFetched.current = true;
    doFetch();
  }, [doFetch]);

  const selectedProjectIdSet = useMemo(
    () => new Set(selectedProjectIds.map(Number)),
    [selectedProjectIds]
  );

  const filteredData = useMemo(
    () => allData.filter((row) => selectedProjectIdSet.has(row.project_id)),
    [allData, selectedProjectIdSet]
  );

  const safeStart = Math.min(windowStart, Math.max(0, filteredData.length - PAGE_SIZE));
  const windowedData = filteredData.slice(safeStart, safeStart + PAGE_SIZE);
  const canPrev = safeStart > 0;
  const canNext = safeStart + PAGE_SIZE < filteredData.length;

  const projectOptions = useMemo(
    () => allData.map((r) => ({ key: String(r.project_id), label: r.project_name as string })),
    [allData]
  );

  const handleRefresh = () => {
    hasFetched.current = false;
    doFetch();
  };

  const handleExport = async () => {
    if (filteredData.length === 0) return;

    const headers = [
      'Project',
      'Overall Progress (%)', 'Tasks Done', 'Total Tasks',
      'Task Completion Rate (%)', 'Completed Last 7d',
      'Overdue Tasks', '% of Active Tasks Overdue',
      'Needs Attention', 'High Risk Decisions', 'Blocked Tasks',
      'Active Decisions', 'Awaiting Approval', 'High Risk Decisions',
      'Under Review', 'Rejected', 'Due Soon (7d)', 'Created Last 7d', 'Completed Last 7d',
      'Active Spreadsheets', 'Active Campaigns', 'Upcoming Meetings',
    ];

    const rows = filteredData.map((row) => {
      const taskTotal = val(row, 'task_total');
      const taskDone = val(row, 'task_done');
      const taskOverdue = val(row, 'task_overdue');
      const taskBlocked = val(row, 'task_blocked');
      const completionPct = pct(taskDone, taskTotal);
      const overduePct = pct(taskOverdue, taskTotal);
      const needsAttention = taskOverdue + taskBlocked;
      return [
        row.project_name,
        completionPct, taskDone, taskTotal,
        completionPct, val(row, 'task_completed_7d'),
        taskOverdue, overduePct,
        needsAttention, val(row, 'decision_high_risk'), taskBlocked,
        val(row, 'decision_total'), val(row, 'decision_pending'), val(row, 'decision_high_risk'),
        val(row, 'task_under_review'), val(row, 'task_rejected'), val(row, 'task_due_soon'),
        val(row, 'task_created_7d'), val(row, 'task_completed_7d'),
        val(row, 'spreadsheet_total'), val(row, 'campaign_active'), val(row, 'meeting_upcoming'),
      ];
    });

    const matrix: (string | number)[][] = [headers, ...rows];
    const blob = await exportMatrixToXLSX(matrix as string[][], 'Project Comparison');
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `project-comparison-${new Date().toISOString().slice(0, 10)}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const toggleExpand = (projectId: number) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };

  if (!loading && allData.length === 0 && !error) return null;

  return (
    <Card className="border-[0.5px] border-gray-200 bg-white shadow-none overflow-hidden">
      {/* Accent stripe */}
      <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />

      <CardContent className="p-0">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Cross-project comparison</h2>
            <p className="text-[12px] text-gray-400 mt-0.5">
              Compare key metrics across all your projects at a glance.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <MultiSelectDropdown
              label="Projects"
              options={projectOptions}
              value={selectedProjectIds}
              onChange={(vals) => {
                setSelectedProjectIds(vals);
                setWindowStart(0);
                setExpandedRows(new Set());
              }}
            />
            <button
              type="button"
              onClick={handleRefresh}
              disabled={loading}
              title="Refresh data"
              className="flex items-center justify-center w-8 h-8 rounded-lg border border-gray-200 bg-white hover:border-gray-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-gray-500 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              type="button"
              onClick={handleExport}
              disabled={loading || filteredData.length === 0}
              title="Export to Excel"
              className="flex items-center justify-center w-8 h-8 rounded-lg border border-gray-200 bg-white hover:border-gray-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Download className="w-3.5 h-3.5 text-gray-500" />
            </button>
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 px-5 py-3 text-[13px] text-red-600 bg-red-50 border-b border-red-100">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {error}
          </div>
        )}

        {/* Body */}
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-14 text-[13px] text-gray-400">
            <Loader2 className="w-4 h-4 animate-spin text-[#3CCED7]" />
            Loading comparison data...
          </div>
        ) : selectedProjectIds.length === 0 ? (
          <div className="py-14 text-center text-[13px] text-gray-400">
            Select at least one project to compare.
          </div>
        ) : filteredData.length === 0 ? (
          <div className="py-14 text-center text-[13px] text-gray-400">
            No data available for the selected projects.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50/50">
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400 whitespace-nowrap">
                    Project
                  </th>
                  <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400 whitespace-nowrap">
                    Overall Progress
                  </th>
                  <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400 whitespace-nowrap">
                    Task Completion Rate
                  </th>
                  <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400 whitespace-nowrap">
                    Overdue Tasks
                  </th>
                  <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400 whitespace-nowrap">
                    Needs Attention
                  </th>
                  <th className="px-3 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {windowedData.map((row, idx) => {
                  const taskTotal = val(row, 'task_total');
                  const taskDone = val(row, 'task_done');
                  const taskOverdue = val(row, 'task_overdue');
                  const taskBlocked = val(row, 'task_blocked');
                  const completedLast7d = val(row, 'task_completed_7d');
                  const completionPct = pct(taskDone, taskTotal);
                  const needsAttention = taskOverdue + taskBlocked;
                  const isExpanded = expandedRows.has(row.project_id);

                  return (
                    <Fragment key={row.project_id}>
                      <tr
                        className={`transition-colors hover:bg-gray-50/60 ${
                          idx < windowedData.length - 1 && !isExpanded ? 'border-b border-gray-50' : ''
                        }`}
                      >
                        {/* Project name */}
                        <td className="px-4 py-3 align-top">
                          {onSelectProject ? (
                            <button
                              type="button"
                              onClick={() => onSelectProject(row.project_id, row.project_name as string)}
                              className="text-[13px] font-semibold text-[#3CCED7] hover:underline whitespace-nowrap text-left"
                            >
                              {row.project_name}
                            </button>
                          ) : (
                            <p className="text-[13px] font-semibold text-gray-900 whitespace-nowrap">
                              {row.project_name}
                            </p>
                          )}
                        </td>

                        {/* Overall Progress */}
                        <SummaryCell
                          value={`${completionPct}%`}
                          bar={completionPct}
                          barColor="#34d399"
                          sub={`${taskDone} / ${taskTotal} tasks done`}
                        />

                        {/* Task Completion Rate */}
                        <SummaryCell
                          value={`${completionPct}%`}
                          valueColor={completionPct > 0 ? '#34d399' : '#334155'}
                          bar={completionPct}
                          barColor="#34d399"
                          sub={`+${completedLast7d} completed last 7d`}
                        />

                        {/* Overdue Tasks */}
                        <SummaryCell
                          value={taskOverdue}
                          valueColor={taskOverdue > 0 ? '#fb7185' : '#334155'}
                          sub={`${pct(taskOverdue, taskTotal)}% of active tasks`}
                        />

                        {/* Needs Attention */}
                        <SummaryCell
                          value={needsAttention}
                          valueColor={needsAttention > 0 ? '#f59e0b' : '#334155'}
                          sub={`${val(row, 'decision_high_risk')} high-risk · ${taskOverdue} overdue · ${taskBlocked} blocked`}
                        />

                        {/* Details toggle */}
                        <td className="px-3 py-3 align-top">
                          <button
                            type="button"
                            onClick={() => toggleExpand(row.project_id)}
                            className="flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-[12px] font-medium text-gray-600 hover:border-gray-300 hover:bg-gray-50 transition-colors whitespace-nowrap"
                          >
                            {isExpanded ? (
                              <>Hide <ChevronUp className="w-3.5 h-3.5" /></>
                            ) : (
                              <>Details <ChevronDown className="w-3.5 h-3.5" /></>
                            )}
                          </button>
                        </td>
                      </tr>

                      {isExpanded && <DetailPanel row={row} />}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Footer pagination */}
        {!loading && filteredData.length > 0 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-gray-100">
            <span className="text-[12px] text-gray-400">
              Showing {safeStart + 1}–{Math.min(safeStart + PAGE_SIZE, filteredData.length)} of{' '}
              {filteredData.length} projects
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setWindowStart((s) => Math.max(0, s - PAGE_SIZE))}
                disabled={!canPrev}
                className="rounded border border-gray-200 p-1 hover:bg-gray-50 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-4 h-4 text-gray-600" />
              </button>
              <button
                type="button"
                onClick={() => setWindowStart((s) => s + PAGE_SIZE)}
                disabled={!canNext}
                className="rounded border border-gray-200 p-1 hover:bg-gray-50 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronRight className="w-4 h-4 text-gray-600" />
              </button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
