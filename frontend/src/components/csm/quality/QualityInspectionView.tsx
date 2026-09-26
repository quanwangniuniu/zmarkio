'use client';

import React, { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import CsmQualityAPI from '@/lib/api/csmQualityApi';
import useQualityFilterParams, { QualityTab } from '@/hooks/useQualityFilterParams';
import {
  QualityConversationRow,
  QualityFilterOptions,
  QualityReport,
} from '@/types/csmQuality';

import ConversationReviewDrawer from './ConversationReviewDrawer';
import QualityConversationTable from './QualityConversationTable';
import QualityExportButton from './QualityExportButton';
import QualityFiltersPanel from './QualityFiltersPanel';
import QualityPagination from './QualityPagination';
import QualityReportView from './QualityReportView';

const PAGE_SIZE = 25;

const TABS: { id: QualityTab; label: string }[] = [
  { id: 'conversations', label: 'Conversations' },
  { id: 'report', label: 'Report' },
];

export function QualityInspectionView() {
  const { filters, tab, page, setFilters, setTab, setPage, clearFilters, activeFilterCount } =
    useQualityFilterParams();

  const [options, setOptions] = useState<QualityFilterOptions | null>(null);
  const [rows, setRows] = useState<QualityConversationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [report, setReport] = useState<QualityReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // Serialised so the effects below re-run on any filter change without
  // depending on object identity.
  const filterKey = JSON.stringify(filters);

  const loadOptions = useCallback(async () => {
    try {
      // Filters go with it: the tallies describe what each option would add to
      // the selection you already have.
      setOptions(await CsmQualityAPI.getFilterOptions(filters));
    } catch {
      toast.error('Could not load the filter options.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  const loadConversations = useCallback(async () => {
    setLoading(true);
    try {
      const data = await CsmQualityAPI.listConversations(filters, page, PAGE_SIZE);
      setRows(data.results);
      setTotal(data.count);
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Could not load conversations.');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, page]);

  const loadReport = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await CsmQualityAPI.getReport(filters));
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Could not load the report.');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  useEffect(() => {
    if (tab === 'conversations') loadConversations();
    else loadReport();
  }, [tab, loadConversations, loadReport]);

  const handleSaved = useCallback(() => {
    // A new annotation changes the rating on the row, the report's counts, and
    // the per-option tallies in the filter bar - which are annotation counts on
    // the Report tab. Refresh the options too, or switching tabs shows stale
    // numbers until the page is reloaded.
    loadOptions();
    if (tab === 'conversations') loadConversations();
    else loadReport();
  }, [tab, loadConversations, loadReport, loadOptions]);

  return (
    <div className="space-y-4 p-6">
      <header>
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Conversation quality</h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Review conversations in the organisations you supervise, annotate them, and report on
            the results.
          </p>
        </div>
      </header>

      <div className="border-b border-slate-200">
        <nav className="flex gap-4" aria-label="Quality inspection tabs">
          {TABS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => setTab(entry.id)}
              aria-current={tab === entry.id ? 'page' : undefined}
              className={`-mb-px border-b-2 px-1 py-2 text-sm font-medium transition ${
                tab === entry.id
                  ? 'border-[#3CCED7] text-[#1a9ba3]'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              {entry.label}
            </button>
          ))}
        </nav>
      </div>

      <QualityFiltersPanel
        filters={filters}
        options={options}
        activeFilterCount={activeFilterCount}
        showDateBasis={tab === 'report'}
        countMode={tab === 'report' ? 'reviews' : 'conversations'}
        onChange={setFilters}
        onClear={clearFilters}
      />

      {tab === 'conversations' ? (
        <div className="rounded-lg border border-slate-200 bg-white">
          <QualityConversationTable
            rows={rows}
            loading={loading}
            onSelect={(row) => setSelectedId(row.id)}
          />
          <QualityPagination
            page={page}
            total={total}
            pageSize={PAGE_SIZE}
            onPageChange={setPage}
          />
        </div>
      ) : (
        <>
          {/* Its own row above the report, because that is what it exports:
              the aggregates, not the conversation list. */}
          <div className="flex">
            <QualityExportButton filters={filters} />
          </div>
          <QualityReportView report={report} loading={loading} />
        </>
      )}

      <ConversationReviewDrawer
        conversationId={selectedId}
        onClose={() => setSelectedId(null)}
        onSaved={handleSaved}
      />
    </div>
  );
}

export default QualityInspectionView;
