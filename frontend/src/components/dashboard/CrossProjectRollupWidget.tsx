'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  EyeOff,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import type { RollupField, RollupProjectResult } from '@/lib/api/dashboardApi';
import type { ProjectData } from '@/lib/api/projectApi';

const PAGE_SIZE = 5;

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
  projects: ProjectData[];
}

export default function CrossProjectRollupWidget({ projects }: CrossProjectRollupWidgetProps) {
  const [fields, setFields] = useState<RollupField[]>([]);
  const [allData, setAllData] = useState<RollupProjectResult[]>([]);
  const [fetchErrors, setFetchErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Client-side display filters (no re-fetch on change)
  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>([]);
  const [selectedFieldKeys, setSelectedFieldKeys] = useState<string[]>([]);
  const [hiddenProjectIds, setHiddenProjectIds] = useState<Set<number>>(new Set());
  const [windowStart, setWindowStart] = useState(0);

  const hasFetched = useRef(false);
  const projectsRef = useRef(projects);
  useEffect(() => { projectsRef.current = projects; }, [projects]);

  // Load available fields on mount
  useEffect(() => {
    DashboardAPI.getRollupFields()
      .then((res) => setFields(res.data))
      .catch(() => setError('Failed to load available fields.'));
  }, []);

  const doFetch = useCallback(async (projectIds: number[], fieldKeys: string[]) => {
    if (projectIds.length === 0 || fieldKeys.length === 0) return;
    setLoading(true);
    try {
      const data = await DashboardAPI.getRollup(projectIds, fieldKeys);
      console.log('[RollupWidget] response:', data);
      setAllData(data.results);
      setFetchErrors(data.errors);
      setWindowStart(0);
      setHiddenProjectIds(new Set());
    } catch (err) {
      console.error('[RollupWidget] fetch failed:', err);
      // On complete failure: render project rows with — for every field
      const fallback = projectsRef.current
        .filter((p) => projectIds.includes(Number(p.id)))
        .map((p) => ({ project_id: Number(p.id), project_name: p.name || '' }));
      setAllData(fallback);
      setFetchErrors(Object.fromEntries(fieldKeys.map((k) => [k, 'failed'])));
      setWindowStart(0);
      setHiddenProjectIds(new Set());
    } finally {
      setLoading(false);
    }
  }, []);

  // Once both projects and fields are available, fetch once
  useEffect(() => {
    console.log('[RollupWidget] hasFetched effect — projects:', projects.length, 'fields:', fields.length, 'hasFetched:', hasFetched.current);
    if (hasFetched.current) return;
    if (projects.length === 0 || fields.length === 0) return;

    const allProjectIds = projects.map((p) => Number(p.id));
    const allFieldKeys = fields.map((f) => f.key);

    console.log('[RollupWidget] dispatching fetch for projectIds:', allProjectIds);
    setSelectedProjectIds(allProjectIds.map(String));
    setSelectedFieldKeys(allFieldKeys);
    hasFetched.current = true;

    doFetch(allProjectIds, allFieldKeys);
  }, [projects, fields, doFetch]);

  // Derived: apply project + eye filters
  const selectedProjectIdSet = useMemo(
    () => new Set(selectedProjectIds.map(Number)),
    [selectedProjectIds]
  );

  const filteredData = useMemo(
    () =>
      allData.filter(
        (row) =>
          selectedProjectIdSet.has(row.project_id) &&
          !hiddenProjectIds.has(row.project_id)
      ),
    [allData, selectedProjectIdSet, hiddenProjectIds]
  );

  const visibleFields = useMemo(
    () => fields.filter((f) => selectedFieldKeys.includes(f.key)),
    [fields, selectedFieldKeys]
  );

  // Clamp windowStart to valid range after hide/filter operations
  const safeStart = Math.min(windowStart, Math.max(0, filteredData.length - PAGE_SIZE));
  const windowedData = filteredData.slice(safeStart, safeStart + PAGE_SIZE);
  const canPrev = safeStart > 0;
  const canNext = safeStart + PAGE_SIZE < filteredData.length;

  const toggleHide = (projectId: number) => {
    setHiddenProjectIds((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };

  const projectOptions = useMemo(
    () => projects.map((p) => ({ key: String(p.id), label: p.name || `Project ${p.id}` })),
    [projects]
  );
  const fieldOptions = useMemo(
    () => fields.map((f) => ({ key: f.key, label: f.label })),
    [fields]
  );

  const handleRefresh = () => {
    const ids = selectedProjectIds.map(Number);
    const keys = selectedFieldKeys.length > 0 ? selectedFieldKeys : fields.map((f) => f.key);
    doFetch(ids, keys);
  };

  if (projects.length === 0) return null;

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
              Compare metrics across all your projects at a glance.
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <MultiSelectDropdown
              label="Projects"
              options={projectOptions}
              value={selectedProjectIds}
              onChange={(vals) => {
                setSelectedProjectIds(vals);
                setHiddenProjectIds(new Set());
                setWindowStart(0);
              }}
            />
            <MultiSelectDropdown
              label="Fields"
              options={fieldOptions}
              value={selectedFieldKeys}
              onChange={setSelectedFieldKeys}
            />
            <button
              type="button"
              onClick={handleRefresh}
              disabled={loading}
              title="Refresh data"
              className="flex items-center justify-center w-8 h-8 rounded-lg border border-gray-200 bg-white hover:border-gray-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 text-gray-500 ${loading ? 'animate-spin' : ''}`}
              />
            </button>
          </div>
        </div>

        {/* Fields load error (widget unusable without field definitions) */}
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
            {hiddenProjectIds.size > 0 ? (
              <>
                All selected projects are hidden.{' '}
                <button
                  type="button"
                  onClick={() => setHiddenProjectIds(new Set())}
                  className="text-[#3CCED7] hover:underline"
                >
                  Show all
                </button>
              </>
            ) : (
              'No data available for the selected projects.'
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50/50">
                  <th className="w-8 px-3 py-2.5" />
                  <th className="px-4 py-2.5 text-left font-medium text-gray-500 whitespace-nowrap">
                    Project
                  </th>
                  {visibleFields.map((f) => (
                    <th
                      key={f.key}
                      className="px-4 py-2.5 text-right font-medium text-gray-500 whitespace-nowrap"
                    >
                      {f.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {windowedData.map((row, idx) => (
                  <tr
                    key={row.project_id}
                    className={`transition-colors hover:bg-gray-50/60 ${
                      idx < windowedData.length - 1 ? 'border-b border-gray-50' : ''
                    }`}
                  >
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        onClick={() => toggleHide(row.project_id)}
                        title="Hide this row"
                        className="text-gray-300 hover:text-gray-500 transition-colors"
                      >
                        <EyeOff className="w-3.5 h-3.5" />
                      </button>
                    </td>
                    <td className="px-4 py-3 font-medium text-gray-900 whitespace-nowrap">
                      {row.project_name}
                    </td>
                    {visibleFields.map((f) => (
                      <td key={f.key} className="px-4 py-3 text-right tabular-nums">
                        {f.key in fetchErrors ? (
                          <span className="text-gray-300">—</span>
                        ) : typeof row[f.key] === 'number' ? (
                          <span className={row[f.key] === 0 ? 'text-gray-400' : 'text-gray-700'}>
                            {(row[f.key] as number).toLocaleString()}
                          </span>
                        ) : (
                          <span className="text-gray-300">—</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
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
              {hiddenProjectIds.size > 0 && (
                <>
                  {' · '}
                  <button
                    type="button"
                    onClick={() => setHiddenProjectIds(new Set())}
                    className="text-[#3CCED7] hover:underline"
                  >
                    {hiddenProjectIds.size} hidden — show all
                  </button>
                </>
              )}
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
