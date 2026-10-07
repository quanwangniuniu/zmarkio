import { create } from 'zustand';
import { TaskData } from '@/types/task';

interface TaskStore {
  tasks: TaskData[];
  currentTask: TaskData | null;
  loading: boolean;
  error: any;

  latestTaskOperationIds: Record<string, string>;

  setTasks: (tasks: TaskData[]) => void;
  setCurrentTask: (task: TaskData | null) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: any) => void;

  updateTask: (
    taskId: number,
    updatedData: Partial<TaskData>,
  ) => void;

  beginTaskOperation: (
    taskId: number,
    operationScope: string,
    operationId: string,
  ) => void;

  resolveTaskFromServer: (
    taskId: number,
    serverPatch: Partial<TaskData>,
    operationScope: string,
    operationId: string,
  ) => boolean;

  rollbackTaskOperation: (
    taskId: number,
    previousPatch: Partial<TaskData>,
    operationScope: string,
    operationId: string,
  ) => boolean;

  updateTasksBulk: (
    taskIds: number[],
    updatedData: Partial<TaskData>,
  ) => void;

  addTask: (task: TaskData) => void;
  removeTask: (taskId: number) => void;
}

function taskOperationKey(
  taskId: number,
  operationScope: string,
): string {
  return `${taskId}:${operationScope}`;
}

function mergeTaskPatch(
  state: Pick<TaskStore, 'tasks' | 'currentTask'>,
  taskId: number,
  patch: Partial<TaskData>,
) {
  return {
    tasks: state.tasks.map((task) =>
      task.id === taskId
        ? { ...task, ...patch }
        : task
    ),
    currentTask:
      state.currentTask?.id === taskId
        ? { ...state.currentTask, ...patch }
        : state.currentTask,
  };
}

export const useTaskStore = create<TaskStore>((set) => ({
  tasks: [],
  currentTask: null,
  loading: false,
  error: null,
  latestTaskOperationIds: {},

  setTasks: (tasks) => set({ tasks }),
  setCurrentTask: (currentTask) => set({ currentTask }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),

  updateTask: (taskId, updatedData) => {
    set((state) => mergeTaskPatch(
      state,
      taskId,
      updatedData,
    ));
  },

  beginTaskOperation: (
    taskId,
    operationScope,
    operationId,
  ) => {
    const key = taskOperationKey(taskId, operationScope);

    set((state) => ({
      latestTaskOperationIds: {
        ...state.latestTaskOperationIds,
        [key]: operationId,
      },
    }));
  },

  resolveTaskFromServer: (
    taskId,
    serverPatch,
    operationScope,
    operationId,
  ) => {
    let applied = false;
    const key = taskOperationKey(taskId, operationScope);

    set((state) => {
      if (
        state.latestTaskOperationIds[key]
        !== operationId
      ) {
        return state;
      }

      applied = true;
      return mergeTaskPatch(
        state,
        taskId,
        serverPatch,
      );
    });

    return applied;
  },

  rollbackTaskOperation: (
    taskId,
    previousPatch,
    operationScope,
    operationId,
  ) => {
    let applied = false;
    const key = taskOperationKey(taskId, operationScope);

    set((state) => {
      if (
        state.latestTaskOperationIds[key]
        !== operationId
      ) {
        return state;
      }

      applied = true;
      return mergeTaskPatch(
        state,
        taskId,
        previousPatch,
      );
    });

    return applied;
  },

  updateTasksBulk: (taskIds, updatedData) => {
    const idSet = new Set(taskIds);

    set((state) => ({
      tasks: state.tasks.map((task) =>
        task.id && idSet.has(task.id)
          ? { ...task, ...updatedData }
          : task
      ),
      currentTask:
        state.currentTask?.id
        && idSet.has(state.currentTask.id)
          ? { ...state.currentTask, ...updatedData }
          : state.currentTask,
    }));
  },

  addTask: (task) => {
    set((state) => ({
      tasks: [task, ...state.tasks],
    }));
  },

  removeTask: (taskId) => {
    set((state) => ({
      tasks: state.tasks.filter(
        (task) => task.id !== taskId
      ),
    }));
  },
}));