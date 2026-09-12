import api from "../api";
import {
  TaskData,
  CreateTaskData,
  TaskApprovalData,
  TaskForwardData,
  TaskLinkData,
  TaskComment,
  TaskRelationsResponse,
  TaskRelationAddRequest,
  TaskAttachment,
  TaskListFilters,
  TaskBulkUpdateRequest,
  TaskBulkActionResponse,
  GanttChartPayload,
  TaskIntelligencePayload,
  WorkCycleHistoryPayload,
  MyActionsPayload,
} from "@/types/task";

export const TaskAPI = {
  // Get available task types
  getTaskTypes: async (): Promise<{ value: string; label: string }[]> => {
    const response = await api.get('/api/task-types/');
    const list = response.data?.task_types;
    return Array.isArray(list) ? list : [];
  },

  // Force create a new task
  forceCreateTask: (data: CreateTaskData) =>
    api.post("/api/tasks/force-create/", data),

  // Get all tasks with optional filters
  getTasks: (params?: TaskListFilters & { content_type?: string; object_id?: string; page?: number }) => {
    const queryParams: any = { ...params };
    if (queryParams.include_subtasks !== undefined) {
      queryParams.include_subtasks = queryParams.include_subtasks.toString();
    }
    if (queryParams.all_projects !== undefined) {
      queryParams.all_projects = queryParams.all_projects.toString();
    }
    if (queryParams.has_parent !== undefined) {
      queryParams.has_parent = queryParams.has_parent.toString();
    }
    return api.get("/api/tasks/", { params: queryParams });
  },

  /** Walk paginated task list until all pages are loaded. */
  getAllTasks: async (
    params?: TaskListFilters & { content_type?: string; object_id?: string },
  ): Promise<TaskData[]> => {
    let allTasks: TaskData[] = [];
    let nextUrl: string | null = null;
    let page = 1;

    do {
      let response: { data: { results?: TaskData[]; next?: string | null } | TaskData[] };

      try {
        if (nextUrl) {
          const parsed = new URL(nextUrl, window.location.origin);
          response = await api.get(parsed.pathname + parsed.search);
        } else {
          response = await TaskAPI.getTasks({ ...params, page });
        }
      } catch (err) {
        const apiData = (err as { response?: { data?: { detail?: string } } })?.response?.data;
        const detail = typeof apiData?.detail === 'string' ? apiData.detail : '';
        if (allTasks.length > 0 && /invalid page/i.test(detail)) {
          break;
        }
        throw err;
      }

      const responseData = response.data;
      const pageTasks = Array.isArray(responseData)
        ? responseData
        : (responseData.results ?? []);
      allTasks = allTasks.concat(pageTasks);

      nextUrl = Array.isArray(responseData) ? null : (responseData.next ?? null);
      page += 1;
      if (page > 100) break;
    } while (nextUrl);

    return allTasks;
  },

  getTasksGantt: async (params?: { project_id?: number | string }): Promise<GanttChartPayload> => {
    const response = await api.get("/api/tasks/gantt/", { params });
    return response.data as GanttChartPayload;
  },

  /**
   * Get a specific task by ID.
   * Pass internalRefetch after in-page saves so server-side TASK_OPEN is not counted again.
   */
  getTask: (
    taskId: number | string,
    options?: { internalRefetch?: boolean; projectId?: number | string | null },
  ) =>
    api.get(`/api/tasks/${taskId}/`, {
      headers: options?.internalRefetch
        ? { 'X-Internal-Refetch': '1' }
        : undefined,
      params: options?.projectId
        ? { project_id: options.projectId }
        : undefined,
    }),

  // Update a task
  updateTask: (
    taskId: number | string,
    data: Partial<TaskData>,
    operationId?: string,
  ) =>
    api.patch(`/api/tasks/${taskId}/`, data, {
      params: operationId
        ? { operation_id: operationId }
        : undefined,
    }),

  // Project-scoped tag catalog (aggregated from all tasks in the project)
  getTagCatalog: async (projectId: number | string): Promise<{ name: string; color: string }[]> => {
    const response = await api.get('/api/tasks/tag-catalog/', { params: { project_id: projectId } });
    const list = response.data?.tags;
    return Array.isArray(list) ? list : [];
  },

  deleteTag: async (projectId: number | string, name: string): Promise<void> => {
    try {
      await api.post('/api/tasks/tag-catalog/delete/', { name }, { params: { project_id: projectId } });
    } catch (error) {
      const statusCode = (error as any)?.response?.status;
      if (![404, 405, 500].includes(statusCode)) {
        throw error;
      }
      await api.delete('/api/tasks/tag-catalog/', { params: { project_id: projectId, name } });
    }
  },

  pinTask: (taskId: number | string) => api.post(`/api/tasks/${taskId}/pin/`),

  unpinTask: (taskId: number | string) => api.delete(`/api/tasks/${taskId}/pin/`),

  bulkAction: async (
    payload: TaskBulkUpdateRequest
  ): Promise<TaskBulkActionResponse> => {
    const response = await api.post('/api/tasks/bulk_action/', payload);
    return response.data as TaskBulkActionResponse;
  },

  // Create a new task
  createTask: (data: CreateTaskData) => api.post("/api/tasks/", data),

  // Link task to a task type object
  linkTask: (taskId: number | string, contentType: string, objectId: string) =>
    api.post(`/api/tasks/${taskId}/link/`, {
      content_type: contentType,
      object_id: objectId,
    }),

  // Submit a task (DRAFT -> SUBMITTED)
  submitTask: (taskId: number | string) =>
    api.post(`/api/tasks/${taskId}/submit/`),

  // Start review for a task
  startReview: (taskId: number | string) =>
    api.post(`/api/tasks/${taskId}/start-review/`),

  // Revise a task
  revise: (taskId: number | string) => api.post(`/api/tasks/${taskId}/revise/`),

  // Make approval decision (approve or reject).
  // Returns 409 when another approver already decided this task first
  // (see isApprovalConflict); callers should refresh and surface the message.
  makeApproval: (taskId: number | string, data: TaskApprovalData) =>
    api.post(`/api/tasks/${taskId}/make-approval/`, data),

  // Lock a task
  lock: (taskId: number | string) => api.post(`/api/tasks/${taskId}/lock/`),

  // Unlock a task (LOCKED -> APPROVED)
  unlock: (taskId: number | string) => api.post(`/api/tasks/${taskId}/unlock/`),

  // Cancel a task
  cancelTask: (taskId: number | string) =>
    api.post(`/api/tasks/${taskId}/cancel/`),

  // Forward task to next approver
  forward: (taskId: number | string, data: TaskForwardData) =>
    api.post(`/api/tasks/${taskId}/forward/`, data),

  // Get approval history
  getApprovalHistory: (taskId: number | string) =>
    api.get(`/api/tasks/${taskId}/approval-history/`),

  getComments: async (taskId: number | string): Promise<TaskComment[]> => {
    const response = await api.get(`/api/tasks/${taskId}/comments/`);
    const data: any = response.data;
    if (Array.isArray(data)) {
      return data as TaskComment[];
    }
    return (data.results || []) as TaskComment[];
  },

  createComment: async (
    taskId: number | string,
    data: { body: string }
  ): Promise<TaskComment> => {
    const response = await api.post(`/api/tasks/${taskId}/comments/`, data);
    return response.data as TaskComment;
  },

  // Delete a task
  deleteTask: (taskId: number | string) => api.delete(`/api/tasks/${taskId}/`),

  // Field history
  getFieldHistory: (taskId: number | string, page = 1, pageSize = 20) =>
    api.get(`/api/tasks/${taskId}/field-history/`, { params: { page, page_size: pageSize } }),

  // Get all relations for a task
  getRelations: async (taskId: number | string): Promise<TaskRelationsResponse> => {
    const response = await api.get(`/api/tasks/${taskId}/relations/`);
    return response.data as TaskRelationsResponse;
  },

  // Add a relation to a task
  addRelation: async (
    taskId: number | string,
    data: TaskRelationAddRequest
  ): Promise<any> => {
    const response = await api.post(`/api/tasks/${taskId}/relations/`, data);
    return response.data;
  },

  // Delete a relation
  deleteRelation: (taskId: number | string, relationId: number | string) =>
    api.delete(`/api/tasks/${taskId}/relations/${relationId}/`),

  // Get all subtasks of a task
  getSubtasks: async (taskId: number | string): Promise<TaskData[]> => {
    const response = await api.get(`/api/tasks/${taskId}/subtasks/`);
    const data: any = response.data;
    if (Array.isArray(data)) {
      return data as TaskData[];
    }
    return (data.results || []) as TaskData[];
  },

  // Add a subtask to a parent task
  addSubtask: async (
    parentTaskId: number | string,
    childTaskId: number | string
  ): Promise<TaskData> => {
    const response = await api.post(`/api/tasks/${parentTaskId}/subtasks/`, {
      child_task_id: childTaskId,
    });
    return response.data as TaskData;
  },

  // Delete a subtask relationship
  deleteSubtask: (parentTaskId: number | string, subtaskId: number | string) =>
    api.delete(`/api/tasks/${parentTaskId}/subtasks/${subtaskId}/`),

  // Get all attachments for a task
  getAttachments: async (taskId: number | string): Promise<TaskAttachment[]> => {
    const response = await api.get(`/api/tasks/${taskId}/attachments/`);
    const data: any = response.data;
    if (Array.isArray(data)) {
      return data as TaskAttachment[];
    }
    return (data.results || []) as TaskAttachment[];
  },

  // Create a new attachment for a task
  createAttachment: async (
    taskId: number | string,
    file: File
  ): Promise<TaskAttachment> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post(`/api/tasks/${taskId}/attachments/`, formData);
    return response.data as TaskAttachment;
  },

  // Delete an attachment
  deleteAttachment: async (
    taskId: number | string,
    attachmentId: number | string
  ): Promise<void> => {
    await api.delete(`/api/tasks/${taskId}/attachments/${attachmentId}/`);
  },

  // Download an attachment (get download URL)
  downloadAttachment: async (
    taskId: number | string,
    attachmentId: number | string
  ): Promise<any> => {
    const response = await api.get(
      `/api/tasks/${taskId}/attachments/${attachmentId}/download/`
    );
    return response.data;
  },


  moveSubtask: (newParentId: number | string, subtaskId: number | string, data: { old_parent_id: number | string }) =>
    api.post(`/api/tasks/${newParentId}/subtasks/${subtaskId}/move/`, data),

  getAutosave: async (type: string): Promise<Record<string, unknown> | null> => {
    const response = await api.get('/api/task-form-autosave/', { params: { type } });
    return response.status === 204 ? null : (response.data as Record<string, unknown>);
  },

  putAutosave: async (type: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> => {
    const response = await api.put('/api/task-form-autosave/', payload, { params: { type } });
    return response.data as Record<string, unknown>;
  },

  deleteAutosave: async (type: string): Promise<void> => {
    await api.delete('/api/task-form-autosave/', { params: { type } });
  },

  getIntelligence: async (params: {
    project_id?: number | string;
    stall_days?: number;
    due_soon_days?: number;
    activity_limit?: number;
    velocity_weeks?: number;
  }): Promise<TaskIntelligencePayload> => {
    const response = await api.get('/api/tasks/intelligence/', { params });
    return response.data as TaskIntelligencePayload;
  },

  getWorkCycle: async (params: {
    project_id?: number | string;
    from?: string;
    to?: string;
  }): Promise<WorkCycleHistoryPayload> => {
    const response = await api.get('/api/tasks/work-cycle/', { params });
    return response.data as WorkCycleHistoryPayload;
  },

  getMyActions: async (params: {
    project_id?: number | string;
    due_soon_days?: number;
  }): Promise<MyActionsPayload> => {
    const response = await api.get('/api/tasks/my-actions/', { params });
    return response.data as MyActionsPayload;
  },

  getStatusReport: (params: {
    project_id: number | string;
    period: 'week' | 'month' | 'custom';
    date_from?: string;
    date_to?: string;
  }) => api.get('/api/tasks/status-report/', { params }),
};

export const TASK_HIERARCHY_CYCLE_CODE = 'task_hierarchy_cycle';

type TaskHierarchyErrorBody = {
  detail?: string;
  code?: string;
  error?: string;
};

/** Map move/add-subtask hierarchy failures; 422 + code indicate a cycle (MED-235). */
export function parseTaskHierarchyApiError(error: unknown): {
  message: string;
  isHierarchyCycle: boolean;
} {
  const response = (error as { response?: { status?: number; data?: TaskHierarchyErrorBody } })
    .response;
  const data = response?.data;
  const isHierarchyCycle =
    response?.status === 422 || data?.code === TASK_HIERARCHY_CYCLE_CODE;
  const message =
    (typeof data === 'string' ? data : undefined) ||
    data?.detail ||
    data?.error ||
    (response?.status === 404
      ? 'Parent move API is unavailable. Restart the backend service and try again.'
      : undefined) ||
    'Failed to update parent task.';
  return { message, isHierarchyCycle };
}

/**
 * True when a task action failed because another approver decided it first
 * (HTTP 409 from make-approval). Lets components refresh the task and show the
 * "already decided" message instead of treating it as a generic failure.
 */
export const isApprovalConflict = (error: unknown): boolean =>
  (error as { response?: { status?: number } })?.response?.status === 409;
