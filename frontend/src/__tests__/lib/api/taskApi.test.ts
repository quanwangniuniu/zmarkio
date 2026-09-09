import { TaskAPI } from '@/lib/api/taskApi';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}));

import api from '@/lib/api';

const mockApi = api as jest.Mocked<typeof api>;

describe('TaskAPI', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.get.mockResolvedValue({ data: [] } as any);
    mockApi.post.mockResolvedValue({ data: {} } as any);
    mockApi.patch.mockResolvedValue({ data: {} } as any);
    mockApi.put.mockResolvedValue({ data: {} } as any);
    mockApi.delete.mockResolvedValue({ data: {} } as any);
  });

  it('covers list/read/write and workflow helpers', async () => {
    mockApi.get.mockResolvedValueOnce({
      data: [{ value: 'task', label: 'Task' }],
    } as any);
    await TaskAPI.getTaskTypes();
    await TaskAPI.forceCreateTask({ title: 't' } as any);
    await TaskAPI.getTasks({ project_id: 1, page: 1 } as any);
    mockApi.get.mockResolvedValueOnce({
      data: { results: [], next: null },
    } as any);
    await TaskAPI.getAllTasks({ project_id: 1 } as any);
    mockApi.get.mockResolvedValueOnce({ data: { tasks: [] } } as any);
    await TaskAPI.getTasksGantt({ project_id: 1 });
    await TaskAPI.getTask(1);
    await TaskAPI.updateTask(1, { title: 'x' } as any);
    mockApi.get.mockResolvedValueOnce({ data: [] } as any);
    await TaskAPI.getTagCatalog(1);
    await TaskAPI.deleteTag(1, 'tag');
    await TaskAPI.pinTask(1);
    await TaskAPI.unpinTask(1);
    await TaskAPI.bulkAction({ action: 'delete', task_ids: [1] } as any);
    await TaskAPI.createTask({ title: 'n' } as any);
    await TaskAPI.linkTask(1, 'decision', '9');
    await TaskAPI.submitTask(1);
    await TaskAPI.startReview(1);
    await TaskAPI.revise(1);
    await TaskAPI.makeApproval(1, { decision: 'approved' } as any);
    await TaskAPI.lock(1);
    await TaskAPI.unlock(1);
    await TaskAPI.cancelTask(1);
    await TaskAPI.forward(1, { to_user_id: 2 } as any);
    await TaskAPI.getApprovalHistory(1);
    expect(mockApi.get).toHaveBeenCalled();
    expect(mockApi.post).toHaveBeenCalled();
  });

  it('covers comments, relations, subtasks, attachments, autosave', async () => {
    mockApi.get.mockResolvedValue({ data: [] } as any);
    await TaskAPI.getComments(1);
    await TaskAPI.createComment(1, { body: 'hi' });
    await TaskAPI.deleteTask(1);
    await TaskAPI.getFieldHistory(1, 1, 10);
    mockApi.get.mockResolvedValueOnce({ data: { relations: [] } } as any);
    await TaskAPI.getRelations(1);
    await TaskAPI.addRelation(1, { type: 'blocks', target_id: 2 } as any);
    await TaskAPI.deleteRelation(1, 9);
    mockApi.get.mockResolvedValueOnce({ data: [] } as any);
    await TaskAPI.getSubtasks(1);
    await TaskAPI.addSubtask(1, 2);
    await TaskAPI.deleteSubtask(1, 2);
    mockApi.get.mockResolvedValueOnce({ data: [] } as any);
    await TaskAPI.getAttachments(1);
    await TaskAPI.createAttachment(1, new File(['x'], 'a.txt'));
    await TaskAPI.deleteAttachment(1, 3);
    mockApi.get.mockResolvedValueOnce({ data: { url: '/x' } } as any);
    await TaskAPI.downloadAttachment(1, 3);
    await TaskAPI.moveSubtask(2, 3, { old_parent_id: 1 });
    mockApi.get.mockResolvedValueOnce({ data: null, status: 204 } as any);
    await TaskAPI.getAutosave('task');
    await TaskAPI.putAutosave('task', { title: 'draft' });
    await TaskAPI.deleteAutosave('task');
    await TaskAPI.getIntelligence({ project_id: 1 } as any);
    await TaskAPI.getWorkCycle({ project_id: 1 } as any);
    await TaskAPI.getMyActions({ project_id: 1 } as any);
    await TaskAPI.getStatusReport({ project_id: 1, period: 'week' });
    expect(mockApi.delete).toHaveBeenCalled();
  });
});
