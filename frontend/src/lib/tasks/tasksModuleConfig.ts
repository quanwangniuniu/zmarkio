import type { TasksTab } from '@/components/tasks/TabNav';
import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyOptionConfig, createOptionTarget, preferredOption } from '@/lib/orgCustomization/optionConfig';
import { TASK_STATUS_BY_VALUE, TASK_STATUS_VALUES } from '@/lib/tasks/taskStatuses';

export const TASKS_TAB = createOptionTarget({
  summary: 'Summary',
  tasks: 'Tasks',
  board: 'Board',
  gantt: 'Gantt',
  insights: 'Insights',
  my_actions: 'My Actions',
  planning: 'Planning',
  status_reports: 'Status Reports',
});

export const TASKS_FILTER = createOptionTarget({
  assignee: 'Assignee',
  owner: 'Owner',
  type: 'Work type',
  status: 'Status',
  priority: 'Priority',
  tags: 'Tags',
  project: 'Project',
  subtasks: 'Subtasks',
  due_date: 'Due date',
  created_date: 'Created date',
});

export const TASKS_FILTER_STATUS = createOptionTarget(
  Object.fromEntries(TASK_STATUS_VALUES.map((value) => [value, TASK_STATUS_BY_VALUE[value].label])),
);

const tasksModuleConfigLoader = createConfigLoader(
  (projectId) => fetchOptionConfig('tasks', projectId),
  (config) => applyOptionConfig(config.options, {
    tab: TASKS_TAB,
    filter: TASKS_FILTER,
    filter_status: TASKS_FILTER_STATUS,
  }),
);

export const loadTasksModuleConfig = tasksModuleConfigLoader.load;
export const storeTasksModuleConfig = tasksModuleConfigLoader.store;

const toTab = (value: string) => value.replace('_', '-') as TasksTab;

export const visibleTasksTabs = (): TasksTab[] => TASKS_TAB.values.map(toTab);

export const defaultTasksTab = (): TasksTab => toTab(preferredOption(TASKS_TAB, 'tasks'));

export const tasksTabLabel = (tab: TasksTab): string => TASKS_TAB.labels[tab.replace('-', '_')];

export const statusFilterOptions = () =>
  TASKS_FILTER_STATUS.values.map((value) => ({ value, label: TASK_STATUS_BY_VALUE[value].label }));
