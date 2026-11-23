'use client';

import { useEffect, useMemo, useState } from 'react';
import { CalendarClock, CheckSquare, GitBranch, Headset, LayoutList, Megaphone, PanelLeft, type LucideIcon } from 'lucide-react';
import toast from 'react-hot-toast';
import { useDispatch, useSelector } from 'react-redux';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { storeCsmConfig } from '@/lib/csm/csmConfig';
import { storeCampaignConfig } from '@/lib/campaigns/campaignConfig';
import { storeDecisionConfig } from '@/lib/decisions/decisionConfig';
import { storeMeetingsConfig } from '@/lib/meetings/meetingsConfig';
import { storeNavigationConfig } from '@/lib/navigation/navigationConfig';
import { useProjectStore } from '@/lib/projectStore';
import { storeTaskConfig } from '@/lib/tasks/taskConfig';
import type { AppDispatch, RootState } from '@/store';
import { loadSystemConfig, saveSystemConfig, selectSystemConfig, selectSystemConfigSaving } from '@/store/modules/orgCustomization';
import { getTaskTypeDefinition } from '@/lib/tasks/taskTypes';
import { getDefaultFieldOptions } from '@/lib/tasks/typeFieldSchemas';
import type { OptionConfig, SystemConfig, TaskConfig, TaskFieldOptionConfig } from '@/types/orgCustomization';

const headerCell = 'px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400';
const bodyCell = 'px-4 py-2 text-sm text-gray-900 dark:text-gray-100';
const tableClass =
  'min-w-full divide-y divide-gray-200 rounded-lg border border-gray-200 dark:divide-gray-700 dark:border-gray-700';
const bodyClass = 'divide-y divide-gray-200 dark:divide-gray-700';
const sectionTitle = 'text-lg font-semibold text-gray-900 dark:text-gray-100';

type ConfigListKey = 'task_types' | 'task_statuses' | 'task_priorities';

type ModuleKey = 'csm' | 'decisions' | 'campaigns' | 'navigation' | 'meetings';

const MODULE_SECTIONS: Record<ModuleKey, { title: string; fields: Record<string, string> }> = {
  csm: { title: 'CSM', fields: { ticket_priority: 'Ticket Priorities' } },
  decisions: {
    title: 'Decisions',
    fields: {
      signal_metric: 'Signal Metrics',
      signal_movement: 'Signal Movements',
      signal_period: 'Signal Periods',
      review_quality: 'Review Quality',
      risk_level: 'Risk Levels',
    },
  },
  campaigns: {
    title: 'Campaigns',
    fields: {
      objective: 'Objectives',
      platform: 'Platforms',
      snapshot_milestone: 'Snapshot Milestones',
      snapshot_metric: 'Snapshot Metrics',
      status: 'Campaign Statuses',
      checkin_sentiment: 'Check-in Sentiments',
    },
  },
  navigation: {
    title: 'Sidebar Navigation',
    fields: { overview: 'Overview', manage: 'Manage', content: 'Content', collaborate: 'Collaborate', tools: 'Tools' },
  },
  meetings: {
    title: 'Meetings',
    fields: {
      filter: 'Advanced Filters',
      filter_type: 'Meeting Type Filter',
      section: 'Detail Sections',
      panel: 'Detail Panels',
    },
  },
};

const MODULE_KEYS = Object.keys(MODULE_SECTIONS) as ModuleKey[];

type ConfigPage = 'task' | ModuleKey;

const PAGE_ICONS: Record<ConfigPage, LucideIcon> = {
  task: CheckSquare,
  csm: Headset,
  decisions: GitBranch,
  campaigns: Megaphone,
  navigation: PanelLeft,
  meetings: CalendarClock,
};

const CONFIG_PAGES: { key: ConfigPage; title: string; icon: LucideIcon; items: { id: string; title: string }[] }[] = [
  {
    key: 'task',
    title: 'Tasks',
    icon: PAGE_ICONS.task,
    items: [
      { id: 'task-types', title: 'Task Types' },
      { id: 'task-statuses', title: 'Task Statuses' },
      { id: 'task-priorities', title: 'Task Priorities' },
      { id: 'task-dropdown-options', title: 'Dropdown Options' },
    ],
  },
  ...MODULE_KEYS.map((module) => ({
    key: module,
    title: MODULE_SECTIONS[module].title,
    icon: PAGE_ICONS[module],
    items: Object.entries(MODULE_SECTIONS[module].fields).map(([field, title]) => ({ id: `${module}-${field}`, title })),
  })),
];

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

const findInvalidOption = (options: OptionConfig[]): string | null => {
  const disabledDefault = options.find((row) => row.is_default && !row.enabled);
  if (disabledDefault) return `${disabledDefault.default_label} is the default and cannot be disabled.`;
  const longLabel = options.find((row) => (row.label ?? '').length > 100);
  if (longLabel) return `${longLabel.default_label} label must be 100 characters or fewer.`;
  return null;
};

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

function OptionConfigTables({ module, options, fieldTitles, onChange }: {
  module: ModuleKey;
  options: OptionConfig[];
  fieldTitles: Record<string, string>;
  onChange: (target: OptionConfig, patch: Partial<OptionConfig>) => void;
}) {
  return (
    <>
      {Object.entries(fieldTitles).map(([field, title]) => {
        const rows = options.filter((row) => row.field === field);
        const editable = new Set(rows[0]?.editable ?? []);
        return (
          <div key={field} id={`${module}-${field}`} className="scroll-mt-32 space-y-1">
            <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">{title}</h3>
            <table className={tableClass}>
              <thead>
                <tr>
                  {editable.has('enabled') && <th className={headerCell}>Enabled</th>}
                  <th className={headerCell}>Option</th>
                  {editable.has('label') && <th className={headerCell}>Label</th>}
                  {editable.has('color') && <th className={headerCell}>Color</th>}
                  {editable.has('order') && <th className={headerCell}>Order</th>}
                </tr>
              </thead>
              <tbody className={bodyClass}>
                {rows.map((row) => (
                  <tr key={row.value}>
                    {editable.has('enabled') && (
                      <td className={bodyCell}>
                        <Checkbox
                          checked={row.enabled}
                          disabled={row.is_default}
                          onCheckedChange={(checked) => onChange(row, { enabled: checked === true })}
                          aria-label={`Enable ${row.default_label}`}
                        />
                      </td>
                    )}
                    <td className={bodyCell}>{row.default_label}</td>
                    {editable.has('label') && (
                      <td className={bodyCell}>
                        <LabelInput value={row.label} placeholder={row.default_label} onChange={(label) => onChange(row, { label })} />
                      </td>
                    )}
                    {editable.has('color') && (
                      <td className={bodyCell}>
                        <input
                          type="color"
                          className="h-9 w-14 cursor-pointer rounded border border-gray-200 dark:border-gray-700"
                          value={row.color ?? '#94a3b8'}
                          onChange={(e) => onChange(row, { color: e.target.value })}
                          aria-label={`${row.default_label} color`}
                        />
                      </td>
                    )}
                    {editable.has('order') && (
                      <td className={bodyCell}>
                        <OrderInput value={row.order} onChange={(order) => onChange(row, { order })} />
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}

export default function SystemConfigPage() {
  const activeProject = useProjectStore((state) => state.activeProject);
  const projectId = activeProject?.id;
  const [config, setConfig] = useState<TaskConfig | null>(null);
  const [optionRows, setOptionRows] = useState<OptionRow[]>([]);
  const [optionType, setOptionType] = useState('');
  const [moduleOptions, setModuleOptions] = useState<Record<ModuleKey, OptionConfig[]>>({ csm: [], decisions: [], campaigns: [], navigation: [], meetings: [] });
  const [activePage, setActivePage] = useState<ConfigPage | 'all'>('all');
  const [scrollTarget, setScrollTarget] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState('task-types');

  useEffect(() => {
    if (!scrollTarget) return;
    document.getElementById(scrollTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setScrollTarget(null);
  }, [scrollTarget, activePage]);

  useEffect(() => {
    const ids = CONFIG_PAGES.filter((page) => activePage === 'all' || page.key === activePage).flatMap((page) =>
      page.items.map((item) => item.id),
    );
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.find((entry) => entry.isIntersecting);
        if (visible) setActiveSection(visible.target.id);
      },
      { rootMargin: '-130px 0px -60% 0px' },
    );
    ids.forEach((id) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, [activePage, config]);

  const goTo = (page: ConfigPage | 'all', id: string, target = id) => {
    setActivePage(page);
    setActiveSection(id);
    setScrollTarget(target);
  };

  const loadConfig = (next: SystemConfig) => {
    setConfig({ ...next.task, can_edit: next.can_edit });
    setOptionRows(buildOptionRows(next.task.task_field_options));
    setModuleOptions({ csm: next.csm.options, decisions: next.decisions.options, campaigns: next.campaigns.options, navigation: next.navigation.options, meetings: next.meetings.options });
  };

  const dispatch = useDispatch<AppDispatch>();
  const systemConfig = useSelector((state: RootState) => selectSystemConfig(state, projectId));
  const saving = useSelector(selectSystemConfigSaving);

  useEffect(() => {
    if (!projectId) return;
    dispatch(loadSystemConfig(projectId))
      .unwrap()
      .catch((message) => toast.error(typeof message === 'string' ? message : 'Failed to load system configuration'));
  }, [dispatch, projectId]);

  useEffect(() => {
    if (systemConfig) loadConfig(systemConfig);
    else setConfig(null);
  }, [systemConfig]);

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

  const updateModuleOption = (module: ModuleKey) => (target: OptionConfig, patch: Partial<OptionConfig>) =>
    setModuleOptions((prev) => ({
      ...prev,
      [module]: prev[module].map((row) => (row === target ? { ...row, ...patch } : row)),
    }));

  const handleSave = async () => {
    if (!config || !projectId) return;
    const invalid = findInvalidOption(MODULE_KEYS.flatMap((module) => moduleOptions[module]));
    if (invalid) {
      toast.error(invalid);
      return;
    }
    try {
      const saved = await dispatch(saveSystemConfig({
        projectId,
        config: {
          task: {
            task_types: config.task_types,
            task_statuses: config.task_statuses,
            task_priorities: config.task_priorities,
            task_field_options: optionRowsToSave(optionRows),
          },
          csm: { options: moduleOptions.csm },
          decisions: { options: moduleOptions.decisions },
          campaigns: { options: moduleOptions.campaigns },
          navigation: { options: moduleOptions.navigation },
          meetings: { options: moduleOptions.meetings },
        },
      })).unwrap();
      storeTaskConfig(projectId, { ...saved.task, can_edit: saved.can_edit });
      storeCsmConfig(projectId, { ...saved.csm, can_edit: saved.can_edit });
      storeDecisionConfig(projectId, { ...saved.decisions, can_edit: saved.can_edit });
      storeCampaignConfig(projectId, { ...saved.campaigns, can_edit: saved.can_edit });
      storeNavigationConfig(projectId, { ...saved.navigation, can_edit: saved.can_edit });
      storeMeetingsConfig(projectId, { ...saved.meetings, can_edit: saved.can_edit });
      toast.success('System configuration saved');
    } catch (message) {
      toast.error(typeof message === 'string' ? message : 'Failed to save system configuration');
    }
  };

  return (
    <ProtectedRoute>
      <DashboardLayout hideRightPanel mainClassName="p-0">
        <div className="p-6 space-y-6">
          <div className="sticky -top-3 z-20 -mx-6 -mt-6 flex items-start justify-between gap-4 border-b border-gray-200 bg-[#F7F8FA] px-6 pb-4 pt-6 dark:border-gray-800 dark:bg-gray-950 sm:-top-5">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">System Configuration</h1>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Choose which task types, CSM, Decisions, Campaigns, Meetings and sidebar options {activeProject?.name ?? 'this project'} uses, and rename,
                recolor or reorder them.
              </p>
            </div>
            {config?.can_edit && (
              <Button onClick={handleSave} disabled={saving}>
                {saving ? 'Saving...' : 'Save'}
              </Button>
            )}
          </div>

          {!config ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {projectId ? 'Loading...' : 'Select a project to configure.'}
            </p>
          ) : !config.can_edit ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Only the organization admin or the project owner can change the system configuration.
            </p>
          ) : (
            <div id="config-content" className="flex scroll-mt-32 flex-col gap-6 md:flex-row">
              <nav
                aria-label="Configuration sections"
                className="shrink-0 rounded-lg border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900 md:sticky md:top-32 md:max-h-[calc(100vh-12rem)] md:w-60 md:self-start md:overflow-y-auto"
              >
                <p className="px-3 pb-2 pt-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">Sections</p>
                <button
                  type="button"
                  onClick={() => goTo('all', CONFIG_PAGES[0].items[0].id, 'config-content')}
                  aria-current={activePage === 'all' ? 'page' : undefined}
                  className={`mb-1 flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors ${
                    activePage === 'all'
                      ? 'bg-[#3CCED7]/10 text-gray-900 dark:text-gray-100'
                      : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800'
                  }`}
                >
                  <LayoutList className={`size-4 shrink-0 ${activePage === 'all' ? 'text-[#3CCED7]' : 'text-gray-400'}`} />
                  <span className="flex-1">All sections</span>
                  <span className="text-xs font-normal text-gray-400">
                    {CONFIG_PAGES.reduce((total, page) => total + page.items.length, 0)}
                  </span>
                </button>
                <ul className="space-y-1">
                  {CONFIG_PAGES.map((page) => {
                    const active = activePage === page.key;
                    const Icon = page.icon;
                    return (
                      <li key={page.key}>
                        <button
                          type="button"
                          onClick={() => goTo(page.key, page.items[0].id, 'config-content')}
                          aria-current={active ? 'page' : undefined}
                          className={`flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors ${
                            active
                              ? 'bg-[#3CCED7]/10 text-gray-900 dark:text-gray-100'
                              : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800'
                          }`}
                        >
                          <Icon className={`size-4 shrink-0 ${active ? 'text-[#3CCED7]' : 'text-gray-400'}`} />
                          <span className="flex-1">{page.title}</span>
                          <span className="text-xs font-normal text-gray-400">{page.items.length}</span>
                        </button>
                        {active && (
                          <ul className="mb-1 mt-1 space-y-0.5">
                            {page.items.map((item) => (
                              <li key={item.id} className="relative">
                                {activeSection === item.id && (
                                  <span className="absolute left-6 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-[#3CCED7]" />
                                )}
                                <button
                                  type="button"
                                  onClick={() => goTo(page.key, item.id)}
                                  className={`w-full rounded-md py-1.5 pl-9 pr-3 text-left text-[13px] transition-colors ${
                                    activeSection === item.id
                                      ? 'font-medium text-gray-900 dark:text-gray-100'
                                      : 'text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100'
                                  }`}
                                >
                                  {item.title}
                                </button>
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </nav>
              <div className="min-w-0 flex-1 space-y-6">
              {(activePage === 'all' || activePage === 'task') && (
              <section className="space-y-3">
                <h2 className={sectionTitle}>Tasks</h2>
              <div id="task-types" className="scroll-mt-32 space-y-1">
                <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Task Types</h3>
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
              </div>

              <div id="task-statuses" className="scroll-mt-32 space-y-1">
                <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Task Statuses</h3>
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
              </div>

              <div id="task-priorities" className="scroll-mt-32 space-y-1">
                <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Task Priorities</h3>
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
              </div>

              <div id="task-dropdown-options" className="scroll-mt-32 space-y-1">
                <div className="flex items-center gap-3">
                  <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Dropdown Options</h3>
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
              </div>

              </section>
              )}

              {MODULE_KEYS.filter((module) => activePage === 'all' || module === activePage).map((module) => (
                <section key={module} className="space-y-3">
                  <h2 className={sectionTitle}>{MODULE_SECTIONS[module].title}</h2>
                  <OptionConfigTables
                    module={module}
                    options={moduleOptions[module]}
                    fieldTitles={MODULE_SECTIONS[module].fields}
                    onChange={updateModuleOption(module)}
                  />
                </section>
              ))}
              </div>
            </div>
          )}
        </div>
      </DashboardLayout>
    </ProtectedRoute>
  );
}
