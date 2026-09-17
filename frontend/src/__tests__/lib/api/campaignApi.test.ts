import { CampaignAPI } from '@/lib/api/campaignApi';

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

describe('CampaignAPI', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.get.mockResolvedValue({ data: {} } as any);
    mockApi.post.mockResolvedValue({ data: {} } as any);
    mockApi.patch.mockResolvedValue({ data: {} } as any);
    mockApi.delete.mockResolvedValue({ data: {} } as any);
  });

  it('covers CRUD and related list endpoints', async () => {
    await CampaignAPI.getCampaigns({ project: '1', status: 'active' });
    await CampaignAPI.getCampaign('c1');
    await CampaignAPI.createCampaign({ name: 'n' } as any);
    await CampaignAPI.updateCampaign('c1', { name: 'n2' } as any);
    await CampaignAPI.deleteCampaign('c1');
    await CampaignAPI.getTaskLinks('c1');
    await CampaignAPI.getActivityTimeline('c1', { page: 1 });
    await CampaignAPI.getStatusHistory('c1');
    await CampaignAPI.getCheckIns('c1', { sentiment: 'positive' });
    await CampaignAPI.createCheckIn('c1', { note: 'ok' } as any);
    await CampaignAPI.updateCheckIn('c1', 'i1', { note: 'upd' } as any);
    await CampaignAPI.deleteCheckIn('c1', 'i1');

    expect(mockApi.get).toHaveBeenCalled();
    expect(mockApi.post).toHaveBeenCalled();
    expect(mockApi.patch).toHaveBeenCalled();
    expect(mockApi.delete).toHaveBeenCalled();
  });

  it('maps status transitions and rejects unknown ones', async () => {
    await CampaignAPI.transitionStatus('c1', 'start-testing', 'note');
    expect(mockApi.post).toHaveBeenCalledWith(
      '/api/campaigns/c1/start-testing/',
      { status_note: 'note' }
    );
    expect(() => CampaignAPI.transitionStatus('c1', 'nope')).toThrow(
      'Unknown transition'
    );
  });

  it('creates/updates snapshots with FormData and JSON paths', async () => {
    const file = new File(['x'], 'shot.png', { type: 'image/png' });

    await CampaignAPI.createSnapshot('c1', {
      milestone_type: 'launch',
      spend: 10,
      metric_type: 'roas',
      metric_value: 2,
      percentage_change: 1,
      notes: 'n',
      additional_metrics: { a: 1 },
      screenshot: file,
    } as any);
    expect(mockApi.post.mock.calls.at(-1)?.[1]).toBeInstanceOf(FormData);

    await CampaignAPI.createSnapshot('c1', {
      milestone_type: 'launch',
      spend: 10,
      metric_type: 'roas',
      metric_value: 2,
      notes: 'n',
      additional_metrics: { a: 1 },
    } as any);
    expect(mockApi.post.mock.calls.at(-1)?.[1]).toEqual(
      expect.objectContaining({ spend: 10, notes: 'n' })
    );

    await CampaignAPI.updateSnapshot('c1', 's1', {
      milestone_type: 'mid',
      spend: 5,
      metric_type: 'cpa',
      metric_value: 1,
      percentage_change: 2,
      notes: 'upd',
      additional_metrics: { b: 2 },
      screenshot: file,
    } as any);
    expect(mockApi.patch.mock.calls.at(-1)?.[1]).toBeInstanceOf(FormData);

    await CampaignAPI.updateSnapshot('c1', 's1', {
      milestone_type: 'mid',
      spend: 5,
      metric_type: 'cpa',
      metric_value: 1,
      percentage_change: 2,
      notes: 'upd',
      additional_metrics: { b: 2 },
    } as any);
    expect(mockApi.patch.mock.calls.at(-1)?.[1]).toEqual(
      expect.objectContaining({ spend: 5 })
    );

    await CampaignAPI.getSnapshots('c1', { milestone_type: 'launch' });
    await CampaignAPI.getSnapshot('c1', 's1');
    await CampaignAPI.deleteSnapshot('c1', 's1');
    await CampaignAPI.uploadScreenshot('c1', 's1', file);
  });

  it('covers template endpoints', async () => {
    await CampaignAPI.getTemplates({ search: 'x' });
    await CampaignAPI.getTemplate('t1');
    await CampaignAPI.createTemplate({ name: 't' } as any);
    await CampaignAPI.updateTemplate('t1', { name: 't2' } as any);
    await CampaignAPI.deleteTemplate('t1');
    await CampaignAPI.createCampaignFromTemplate('t1', { name: 'c' } as any);
    await CampaignAPI.saveCampaignAsTemplate('c1', { name: 't' } as any);
    expect(mockApi.get).toHaveBeenCalled();
    expect(mockApi.post).toHaveBeenCalled();
  });
});
