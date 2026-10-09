import type { DashboardLayoutConfiguration } from '@/types/dashboardLayout';

/** Test API data only; production obtains its configuration from Django. */
export const dashboardLayoutConfiguration: DashboardLayoutConfiguration = {
  "columns": 12,
  "row_height": 48,
  "gap": 12,
  "resize_step": 10,
  "max_y": 999,
  "min_width": 1,
  "min_height": 3,
  "max_height": 30,
  "max_widgets": 100,
  "position_epsilon": 1e-07,
  "widgets": [
    {"id": "overall-progress", "x": 0, "y": 0, "w": 4, "h": 3, "label": "Overall Progress", "min_resize_height": 3},
    {"id": "tasks-completed", "x": 4, "y": 0, "w": 4, "h": 3, "label": "Tasks Completed", "min_resize_height": 3},
    {"id": "task-completion-rate", "x": 8, "y": 0, "w": 4, "h": 3, "label": "Task Completion Rate", "min_resize_height": 3},
    {"id": "overdue-tasks", "x": 0, "y": 3, "w": 6, "h": 3, "label": "Overdue Tasks", "min_resize_height": 3},
    {"id": "needs-attention", "x": 6, "y": 3, "w": 6, "h": 3, "label": "Needs Attention", "min_resize_height": 3},
    {"id": "decisions", "x": 0, "y": 6, "w": 4, "h": 7, "label": "Decisions", "min_resize_height": 3},
    {"id": "tasks", "x": 4, "y": 6, "w": 4, "h": 7, "label": "Tasks", "min_resize_height": 3},
    {"id": "operations", "x": 8, "y": 6, "w": 4, "h": 7, "label": "Operations", "min_resize_height": 3},
    {"id": "task-status", "x": 0, "y": 13, "w": 4, "h": 5, "label": "Task Status Breakdown", "min_resize_height": 3},
    {"id": "task-priority", "x": 4, "y": 13, "w": 4, "h": 5, "label": "Task Priority Distribution", "min_resize_height": 3},
    {"id": "task-types", "x": 8, "y": 13, "w": 4, "h": 6, "label": "Type breakdown", "min_resize_height": 3},
    {"id": "task-trend", "x": 0, "y": 19, "w": 12, "h": 5, "label": "Tasks Created vs Completed", "min_resize_height": 3},
    {"id": "custom-kpis", "x": 0, "y": 24, "w": 12, "h": 4, "label": "Custom KPIs", "min_resize_height": 3},
    {"id": "meetings", "x": 0, "y": 28, "w": 6, "h": 4, "label": "Meetings & action items", "min_resize_height": 4},
    {"id": "activity", "add_x": 0, "x": 6, "y": 28, "w": 6, "h": 4, "label": "Recent activity", "min_resize_height": 4},
    {"id": "audit", "x": 0, "y": 32, "w": 6, "h": 4, "label": "Audit", "min_resize_height": 4},
    {"id": "project-team", "add_x": 0, "x": 6, "y": 32, "w": 6, "h": 6, "label": "Project team", "min_resize_height": 6}
  ]
};
