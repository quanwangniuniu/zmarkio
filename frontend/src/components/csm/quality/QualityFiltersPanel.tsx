'use client';

import React from 'react';
import { QualityDateBasis, QualityFilterOptions, QualityFilters } from '@/types/csmQuality';
import QualityMultiSelect from './QualityMultiSelect';

interface QualityFiltersPanelProps {
  filters: QualityFilters;
  options: QualityFilterOptions | null;
  activeFilterCount: number;
  showDateBasis: boolean;
  onChange: (next: Partial<QualityFilters>) => void;
  onClear: () => void;
}

const CONTROL =
  'h-8 w-full min-w-0 rounded-md border border-slate-300 bg-white px-2 text-sm text-slate-700 focus:border-[#3CCED7] focus:outline-none focus:ring-1 focus:ring-[#3CCED7]';

const UNASSIGNED = 'unassigned';

export function QualityFiltersPanel({
  filters,
  options,
  activeFilterCount,
  showDateBasis,
  onChange,
  onClear,
}: QualityFiltersPanelProps) {
  const agentOptions = [
    { value: UNASSIGNED, label: 'Unassigned' },
    ...(options?.agents ?? []).map((agent) => ({
      value: String(agent.user_id),
      label: agent.name || agent.email,
    })),
  ];

  return (
    <section
      aria-label="Quality inspection filters"
      className="rounded-lg border border-slate-200 bg-white p-3"
    >
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
        <div>
          <label htmlFor="quality-date-from" className="mb-1 block text-xs font-medium text-slate-600">
            From
          </label>
          <input
            id="quality-date-from"
            type="date"
            value={filters.date_from ?? ''}
            onChange={(event) => onChange({ date_from: event.target.value || undefined })}
            className={CONTROL}
          />
        </div>

        <div>
          <label htmlFor="quality-date-to" className="mb-1 block text-xs font-medium text-slate-600">
            To
          </label>
          <input
            id="quality-date-to"
            type="date"
            value={filters.date_to ?? ''}
            onChange={(event) => onChange({ date_to: event.target.value || undefined })}
            className={CONTROL}
          />
        </div>

        <QualityMultiSelect
          label="Agent"
          searchable
          options={agentOptions}
          selected={filters.agent.map(String)}
          onChange={(next) =>
            onChange({
              agent: next.map((value) => (value === UNASSIGNED ? UNASSIGNED : Number(value))),
            })
          }
        />

        <QualityMultiSelect
          label="Queue"
          options={(options?.queues ?? []).map((queue) => ({
            value: String(queue.id),
            label: queue.is_active ? queue.name : `${queue.name} (archived)`,
          }))}
          selected={filters.queue.map(String)}
          onChange={(next) => onChange({ queue: next.map(Number) })}
        />

        <QualityMultiSelect
          label="Channel"
          options={(options?.channels ?? []).map((channel) => ({
            value: channel.value,
            label: channel.label,
          }))}
          selected={filters.channel}
          onChange={(next) => onChange({ channel: next })}
        />

        <QualityMultiSelect
          label="Customer"
          searchable
          options={(options?.customers ?? []).map((customer) => ({
            value: String(customer.id),
            label: customer.name || customer.email,
          }))}
          selected={filters.customer.map(String)}
          onChange={(next) => onChange({ customer: next.map(Number) })}
        />

        <QualityMultiSelect
          label="Tag"
          searchable
          options={(options?.tags ?? []).map((tag) => ({ value: tag, label: tag }))}
          selected={filters.tag}
          onChange={(next) => onChange({ tag: next })}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <QualityMultiSelect
          label="Status"
          options={(options?.statuses ?? []).map((status) => ({
            value: status.value,
            label: status.label,
          }))}
          selected={filters.status}
          onChange={(next) => onChange({ status: next })}
        />

        {showDateBasis && (
          <div>
            <label
              htmlFor="quality-date-basis"
              className="mb-1 block text-xs font-medium text-slate-600"
            >
              Date range applies to
            </label>
            <select
              id="quality-date-basis"
              value={filters.date_basis ?? 'review'}
              onChange={(event) =>
                onChange({ date_basis: event.target.value as QualityDateBasis })
              }
              className={CONTROL}
            >
              <option value="review">Review date</option>
              <option value="conversation">Conversation start</option>
            </select>
          </div>
        )}

        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-slate-500">
            {activeFilterCount === 0
              ? 'No filters applied'
              : `${activeFilterCount} filter${activeFilterCount === 1 ? '' : 's'} applied`}
          </span>
          <button
            type="button"
            onClick={onClear}
            disabled={activeFilterCount === 0}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Clear all
          </button>
        </div>
      </div>
    </section>
  );
}

export default QualityFiltersPanel;
