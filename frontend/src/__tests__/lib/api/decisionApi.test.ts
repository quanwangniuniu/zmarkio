import api from '@/lib/api';
import { DecisionAPI } from '@/lib/api/decisionApi';

jest.mock('@/lib/api', () => ({ __esModule: true, default: { post: jest.fn(), patch: jest.fn() } }));

it('sends topic and parent IDs in the creation body with project context', async () => {
  (api.post as jest.Mock).mockResolvedValue({ data: { id: 113 } });
  const body = { topic: 'pricing', parentDecisionIds: [112] };
  expect(await DecisionAPI.createDraft(10, body)).toEqual({ id: 113 });
  expect(api.post).toHaveBeenCalledWith('/api/decisions/drafts/', body, {
    headers: { 'x-project-id': 10 }, params: { project_id: 10 },
  });
  expect(api.patch).not.toHaveBeenCalled();
});
