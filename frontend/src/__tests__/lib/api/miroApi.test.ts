import { miroApi } from '@/lib/api/miroApi';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));

import api from '@/lib/api';

const mockApi = api as jest.Mocked<typeof api>;

describe('miroApi', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('covers board and item CRUD happy paths', async () => {
    mockApi.get.mockResolvedValue({ data: { results: [{ id: 'b1' }] } } as any);
    mockApi.post.mockResolvedValue({
      data: { id: 'b1', board_id: 'b1', item_id: 'i1' },
    } as any);
    mockApi.patch.mockResolvedValue({ data: { id: 'b1' } } as any);
    mockApi.delete.mockResolvedValue({ data: {} } as any);

    await expect(miroApi.getBoards()).resolves.toEqual([{ id: 'b1' }]);
    mockApi.get.mockResolvedValueOnce({ data: { id: 'b1' } } as any);
    await miroApi.getBoard('b1');
    await miroApi.getLatestProjectBoard(1);
    await miroApi.markBoardAccess('b1');
    await miroApi.createBoard({ project_id: 1, title: 'Board', viewport: { x: 0 } });
    await miroApi.createBoard({ project_id: 1, title: '' });
    await miroApi.updateBoard('b1', { title: 'Renamed' });
    await miroApi.deleteBoard('b1');

    mockApi.get.mockResolvedValueOnce({
      data: [{ id: 'i1', board_id: 'b1' }, { item_id: 'i2', boardId: 'b1' }],
    } as any);
    await miroApi.getBoardItems('b1', true);
    await miroApi.createBoardItem('b1', {
      type: 'text',
      x: 0,
      y: 0,
      width: 10,
      height: 10,
    });
    await miroApi.updateBoardItem('i1', { content: 'hi' });
    await miroApi.deleteBoardItem('i1');
    await miroApi.batchUpdateBoardItems('b1', [{ id: 'i1', content: 'x' }]);
    await miroApi.listBoardRevisions('b1');
    await miroApi.createBoardRevision('b1', {
      snapshot: { viewport: {}, items: [] },
    });
    await miroApi.restoreBoardRevision('b1', 1);
  });

  it('normalizes and surfaces API errors', async () => {
    mockApi.get.mockRejectedValue({
      response: { status: 500, data: { detail: 'boom' } },
    });
    await expect(miroApi.getBoard('missing')).rejects.toMatchObject({
      message: 'boom',
      status: 500,
    });

    mockApi.get.mockResolvedValue({ data: [{ board_id: 'b1' }] } as any);
    await expect(miroApi.getBoardItems('b1')).rejects.toThrow('missing id');
  });
});
