import { applyTaskConfig } from '@/lib/tasks/taskConfig';
import { getTaskPriorityDefinition } from '@/lib/tasks/taskPriorities';
import { getTaskStatusDefinition } from '@/lib/tasks/taskStatuses';
import { getTaskTypeDefinition, TASK_TYPE_SHORT_LABEL_BY_VALUE } from '@/lib/tasks/taskTypes';
import { getTypeSchema } from '@/lib/tasks/typeFieldSchemas';
import type { TaskConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchTaskConfig: jest.fn() }));

const config = (overrides: Partial<TaskConfig>): TaskConfig => ({
  task_types: [],
  task_statuses: [],
  task_priorities: [],
  task_field_options: [],
  status_categories: [],
  ...overrides,
});

const metricOptions = () =>
  getTypeSchema('alert')?.fields.find((field) => field.key === 'metric_key')?.options ?? [];

const customConfig = config({
  task_types: [{
    key: 'budget', default_label: 'Budget', label: 'Spend Request', short_label: 'Spend',
    color: '#000000', enabled: true, order: 0,
  }],
  task_statuses: [{
    key: 'UNDER_REVIEW', default_label: 'Under Review', label: 'In QA', color: null,
    default_category: 'IN_PROGRESS', category: null, order: 2,
  }],
  task_priorities: [{ key: 'HIGHEST', default_label: 'Highest', label: 'Urgent', color: null, order: 0 }],
  task_field_options: [
    { task_type: 'alert', field: 'metric_key', value: 'cpa', label: null, enabled: false, order: 1 },
    { task_type: 'alert', field: 'metric_key', value: 'roas', label: 'Return on ad spend', enabled: true, order: 0 },
    { task_type: 'alert', field: 'metric_key', value: 'spend', label: null, enabled: true, order: 3 },
  ],
});

const resetConfig = config({
  task_types: [{ ...customConfig.task_types[0], label: null, short_label: null, color: null }],
  task_statuses: [{ ...customConfig.task_statuses[0], label: null }],
  task_priorities: [{ ...customConfig.task_priorities[0], label: null }],
});

describe('applyTaskConfig', () => {
  afterEach(() => applyTaskConfig(resetConfig));

  it('overrides type, status and priority labels', () => {
    applyTaskConfig(customConfig);

    expect(getTaskTypeDefinition('budget')).toMatchObject({ label: 'Spend Request', shortLabel: 'Spend', hex: '#000000' });
    expect(TASK_TYPE_SHORT_LABEL_BY_VALUE.budget).toBe('Spend');
    expect(getTaskStatusDefinition('UNDER_REVIEW').label).toBe('In QA');
    expect(getTaskPriorityDefinition('HIGHEST').label).toBe('Urgent');
  });

  it('restores defaults when an override is cleared', () => {
    applyTaskConfig(customConfig);
    applyTaskConfig(resetConfig);

    expect(getTaskTypeDefinition('budget')).toMatchObject({ label: 'Budget', shortLabel: 'Budget', hex: '#3CCED7' });
    expect(getTaskStatusDefinition('UNDER_REVIEW').label).toBe('In review');
    expect(getTaskPriorityDefinition('HIGHEST').label).toBe('Highest');
    expect(metricOptions().map((option) => option.value)).toEqual(
      ['spend', 'cpa', 'ctr', 'roas', 'impressions', 'clicks', 'conversions'],
    );
  });

  it('hides, relabels and reorders dropdown options', () => {
    applyTaskConfig(customConfig);

    const options = metricOptions();
    expect(options[0]).toEqual({ value: 'roas', label: 'Return on ad spend' });
    expect(options.map((option) => option.value).slice(0, 3)).toEqual(['roas', 'ctr', 'spend']);
    expect(options.map((option) => option.value)).not.toContain('cpa');
  });
});
