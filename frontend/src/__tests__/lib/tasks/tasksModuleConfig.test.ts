import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import {
  defaultTasksTab,
  loadTasksModuleConfig,
  statusFilterOptions,
  storeTasksModuleConfig,
  tasksTabLabel,
  TASKS_FILTER,
  visibleTasksTabs,
} from '@/lib/tasks/tasksModuleConfig';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const option = (field: string, value: string, overrides: Partial<OptionConfig> = {}): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: true, order: 0, is_default: false, editable: [], ...overrides,
});

const moduleConfig = (options: OptionConfig[]): OptionModuleConfig => ({ options, can_edit: true });

describe('tasks module config', () => {
  beforeAll(async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(moduleConfig([]));
    await loadTasksModuleConfig(1);
  });

  afterEach(async () => {
    await loadTasksModuleConfig(1);
    storeTasksModuleConfig(1, moduleConfig([]));
  });

  it('shows all tabs, filters and statuses by default', () => {
    expect(visibleTasksTabs()).toEqual(['summary', 'tasks', 'board', 'gantt', 'insights', 'my-actions', 'planning', 'status-reports']);
    expect(defaultTasksTab()).toBe('tasks');
    expect(TASKS_FILTER.values).toHaveLength(10);
    expect(statusFilterOptions().map((o) => o.value)).toEqual(['DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'REJECTED', 'LOCKED', 'CANCELLED']);
  });

  it('renames, hides and reorders tabs', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(moduleConfig([
      option('tab', 'my_actions', { label: 'Mine', order: 0 }),
      option('tab', 'summary', { enabled: false, order: 1 }),
    ]));

    await loadTasksModuleConfig(2);

    expect(fetchOptionConfig).toHaveBeenCalledWith('tasks', 2);
    expect(visibleTasksTabs()).toEqual(['my-actions', 'tasks', 'board', 'gantt', 'insights', 'planning', 'status-reports']);
    expect(tasksTabLabel('my-actions')).toBe('Mine');
  });

  it('falls back to the first visible tab when the default tab is hidden', () => {
    storeTasksModuleConfig(1, moduleConfig([
      option('tab', 'summary', { enabled: false, order: 0 }),
      option('tab', 'tasks', { enabled: false, order: 1 }),
    ]));

    expect(visibleTasksTabs()).not.toContain('tasks');
    expect(defaultTasksTab()).toBe('board');
  });

  it('hides and reorders status filter options', () => {
    storeTasksModuleConfig(1, moduleConfig([
      option('filter_status', 'LOCKED', { enabled: false, order: 0 }),
      option('filter_status', 'CANCELLED', { order: 1 }),
    ]));

    expect(statusFilterOptions().map((o) => o.value)).toEqual(['DRAFT', 'SUBMITTED', 'CANCELLED', 'UNDER_REVIEW', 'APPROVED', 'REJECTED']);
  });
});
