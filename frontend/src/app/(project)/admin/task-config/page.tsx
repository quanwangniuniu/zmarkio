'use client';

import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import Layout from '@/components/layout/Layout';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { getApiErrorDetail } from '@/lib/api/errorMessage';
import { fetchTaskConfig, saveTaskConfig } from '@/lib/api/orgCustomizationApi';
import { applyTaskConfig } from '@/lib/tasks/taskConfig';
import { getTaskTypeDefinition } from '@/lib/tasks/taskTypes';
import { getDefaultFieldOptions } from '@/lib/tasks/typeFieldSchemas';
import type { TaskConfig, TaskFieldOptionConfig } from '@/types/orgCustomization';

const headerCell = 'px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400';
const bodyCell = 'px-4 py-2 text-sm text-gray-900 dark:text-gray-100';
const tableClass =
  'min-w-full divide-y divide-gray-200 rounded-lg border border-gray-200 dark:divide-gray-700 dark:border-gray-700';
const bodyClass = 'divide-y divide-gray-200 dark:divide-gray-700';
const sectionTitle = 'text-lg font-semibold text-gray-900 dark:text-gray-100';

type ConfigListKey = 'task_types' | 'task_statuses' | 'task_priorities';

interface OptionRow extends TaskFieldOptionConfig {
  fieldLabel: string;
  defaultLabel: string;
  defaultOrder: number;
  saved: boolean;
}

const buildOptionRows = (saved: TaskFieldOptionConfig[]): OptionRow[] =>
  getDefaultFieldOptions().flatMap((group) =>
    group.options.map((option, index) => {
      const row = saved.find(
        (item) => item.task_type === group.taskType && item.field === group.field && item.value === option.value,
      );
      return {
        task_type: group.taskType,
        field: group.field,
        value: option.value,
        label: row?.label ?? null,
        enabled: row?.enabled ?? true,
        order: row?.order ?? index,
        fieldLabel: group.fieldLabel,
        defaultLabel: option.label,
        defaultOrder: index,
        saved: Boolean(row),
      };
    }),
  );

const optionGroupKey = (row: OptionRow) => `${row.task_type}.${row.field}`;

const isOptionChanged = (row: OptionRow) =>
  row.saved || Boolean(row.label) || !row.enabled || row.order !== row.defaultOrder;

const optionRowsToSave = (rows: OptionRow[]) => {
  const changedGroups = new Set(rows.filter(isOptionChanged).map(optionGroupKey));
  return rows
    .filter((row) => changedGroups.has(optionGroupKey(row)))
    .map(({ task_type, field, value, label, enabled, order }) => ({ task_type, field, value, label, enabled, order }));
};

function LabelInput({ value, placeholder, onChange }: {
  value: string | null;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return <Input value={value ?? ''} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />;
}

function OrderInput({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  return (
    <Input
      type="number"
      min={0}
      className="w-24"
      value={value}
      onChange={(e) => onChange(Number(e.target.value) || 0)}
    />
  );
}

export default function TaskConfigPage() {
  const [config, setConfig] = useState<TaskConfig | null>(null);
  const [optionRows, setOptionRows] = useState<OptionRow[]>([]);
  const [optionType, setOptionType] = useState('');
  const [saving, setSaving] = useState(false);

  const loadConfig = (next: TaskConfig) => {
    setConfig(next);
    setOptionRows(buildOptionRows(next.task_field_options));
  };

  useEffect(() => {
    fetchTaskConfig()
      .then(loadConfig)
      .catch((error) => toast.error(getApiErrorDetail(error, 'Failed to load task config')));
  }, []);

  const optionTypes = useMemo(
    () => Array.from(new Set(optionRows.map((row) => row.task_type))),
    [optionRows],
  );
  const selectedOptionType = optionType || optionTypes[0] || '';
  const optionGroups = useMemo(() => {
    const groups = new Map<string, OptionRow[]>();
    optionRows
      .filter((row) => row.task_type === selectedOptionType)
      .forEach((row) => groups.set(row.field, [...(groups.get(row.field) ?? []), row]));
    return Array.from(groups.values());
  }, [optionRows, selectedOptionType]);

  const updateItem = (list: ConfigListKey, key: string, patch: Record<string, unknown>) =>
    setConfig((prev) => prev && {
      ...prev,
      [list]: (prev[list] as { key: string }[]).map((item) => (item.key === key ? { ...item, ...patch } : item)),
    });

  const updateOption = (target: OptionRow, patch: Partial<OptionRow>) =>
    setOptionRows((prev) => prev.map((row) => (row === target ? { ...row, ...patch } : row)));

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const saved = await saveTaskConfig({
        task_types: config.task_types,
        task_statuses: config.task_statuses,
        task_priorities: config.task_priorities,
        task_field_options: optionRowsToSave(optionRows),
      });
      loadConfig(saved);
      applyTaskConfig(saved);
      toast.success('Task config saved');
    } catch (error) {
      toast.error(getApiErrorDetail(error, 'Failed to save task config'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ProtectedRoute requireAdmin fallback="/unauthorized">
      <Layout showPermissionRole={true}>
        <div className="p-6 space-y-6">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Task Config</h1>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Choose which task types your organization uses, and rename or reorder types, statuses,
                priorities and dropdown options.
              </p>
            </div>
            <Button onClick={handleSave} disabled={!config || saving}>
              {saving ? 'Saving...' : 'Save'}
            </Button>
          </div>

          {!config ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">Loading...</p>
          ) : (
            <>
              <section className="space-y-2">
                <h2 className={sectionTitle}>Task Types</h2>
                <table className={tableClass}>
                  <thead>
                    <tr>
                      <th className={headerCell}>Enabled</th>
                      <th className={headerCell}>Type</th>
                      <th className={headerCell}>Label</th>
                      <th className={headerCell}>Short label</th>
                      <th className={headerCell}>Color</th>
                      <th className={headerCell}>Order</th>
                    </tr>
                  </thead>
                  <tbody className={bodyClass}>
                    {config.task_types.map((item) => (
                      <tr key={item.key}>
                        <td className={bodyCell}>
                          <Checkbox
                            checked={item.enabled}
                            onCheckedChange={(checked) => updateItem('task_types', item.key, { enabled: checked === true })}
                            aria-label={`Enable ${item.default_label}`}
                          />
                        </td>
                        <td className={bodyCell}>{item.default_label}</td>
                        <td className={bodyCell}>
                          <LabelInput
                            value={item.label}
                            placeholder={item.default_label}
                            onChange={(label) => updateItem('task_types', item.key, { label })}
                          />
                        </td>
                        <td className={bodyCell}>
                          <LabelInput
                            value={item.short_label}
                            placeholder={getTaskTypeDefinition(item.key)?.shortLabel ?? item.default_label}
                            onChange={(short_label) => updateItem('task_types', item.key, { short_label })}
                          />
                        </td>
                        <td className={bodyCell}>
                          <input
                            type="color"
                            className="h-9 w-14 cursor-pointer rounded border border-gray-200 dark:border-gray-700"
                            value={item.color ?? getTaskTypeDefinition(item.key)?.hex ?? '#000000'}
                            onChange={(e) => updateItem('task_types', item.key, { color: e.target.value })}
                            aria-label={`${item.default_label} color`}
                          />
                        </td>
                        <td className={bodyCell}>
                          <OrderInput value={item.order} onChange={(order) => updateItem('task_types', item.key, { order })} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section className="space-y-2">
                <h2 className={sectionTitle}>Task Statuses</h2>
                <table className={tableClass}>
                  <thead>
                    <tr>
                      <th className={headerCell}>Status</th>
                      <th className={headerCell}>Label</th>
                      <th className={headerCell}>Category</th>
                    </tr>
                  </thead>
                  <tbody className={bodyClass}>
                    {config.task_statuses.map((item) => (
                      <tr key={item.key}>
                        <td className={bodyCell}>{item.default_label}</td>
                        <td className={bodyCell}>
                          <LabelInput
                            value={item.label}
                            placeholder={item.default_label}
                            onChange={(label) => updateItem('task_statuses', item.key, { label })}
                          />
                        </td>
                        <td className={bodyCell}>
                          <select
                            className="h-9 rounded-md border border-gray-200 bg-transparent px-2 text-sm dark:border-gray-700"
                            value={item.category ?? item.default_category}
                            onChange={(e) => updateItem('task_statuses', item.key, { category: e.target.value })}
                            aria-label={`${item.default_label} category`}
                          >
                            {config.status_categories.map((category) => (
                              <option key={category.key} value={category.key}>{category.label}</option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section className="space-y-2">
                <h2 className={sectionTitle}>Task Priorities</h2>
                <table className={tableClass}>
                  <thead>
                    <tr>
                      <th className={headerCell}>Priority</th>
                      <th className={headerCell}>Label</th>
                    </tr>
                  </thead>
                  <tbody className={bodyClass}>
                    {config.task_priorities.map((item) => (
                      <tr key={item.key}>
                        <td className={bodyCell}>{item.default_label}</td>
                        <td className={bodyCell}>
                          <LabelInput
                            value={item.label}
                            placeholder={item.default_label}
                            onChange={(label) => updateItem('task_priorities', item.key, { label })}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section className="space-y-3">
                <div className="flex items-center gap-3">
                  <h2 className={sectionTitle}>Dropdown Options</h2>
                  <select
                    className="h-9 rounded-md border border-gray-200 bg-transparent px-2 text-sm dark:border-gray-700"
                    value={selectedOptionType}
                    onChange={(e) => setOptionType(e.target.value)}
                    aria-label="Task type for dropdown options"
                  >
                    {optionTypes.map((type) => (
                      <option key={type} value={type}>{getTaskTypeDefinition(type)?.label ?? type}</option>
                    ))}
                  </select>
                </div>
                {optionGroups.map((rows) => (
                  <div key={rows[0].field} className="space-y-1">
                    <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">{rows[0].fieldLabel}</h3>
                    <table className={tableClass}>
                      <thead>
                        <tr>
                          <th className={headerCell}>Enabled</th>
                          <th className={headerCell}>Option</th>
                          <th className={headerCell}>Label</th>
                          <th className={headerCell}>Order</th>
                        </tr>
                      </thead>
                      <tbody className={bodyClass}>
                        {rows.map((row) => (
                          <tr key={row.value}>
                            <td className={bodyCell}>
                              <Checkbox
                                checked={row.enabled}
                                onCheckedChange={(checked) => updateOption(row, { enabled: checked === true })}
                                aria-label={`Enable ${row.defaultLabel}`}
                              />
                            </td>
                            <td className={bodyCell}>{row.defaultLabel}</td>
                            <td className={bodyCell}>
                              <LabelInput
                                value={row.label}
                                placeholder={row.defaultLabel}
                                onChange={(label) => updateOption(row, { label })}
                              />
                            </td>
                            <td className={bodyCell}>
                              <OrderInput value={row.order} onChange={(order) => updateOption(row, { order })} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </section>
            </>
          )}
        </div>
      </Layout>
    </ProtectedRoute>
  );
}
