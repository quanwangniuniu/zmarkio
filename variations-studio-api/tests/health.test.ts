import { GET } from '@/app/health/route';

import { studioRequest } from './support/requests';

describe('GET /health', () => {
  it('answers without auth', async () => {
    const response = await GET();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ ok: true });
  });

  it('builds requests the route handlers accept', () => {
    const request = studioRequest('/health');

    expect(request.method).toBe('GET');
    expect(request.headers.get('authorization')).toBeNull();
  });
});
