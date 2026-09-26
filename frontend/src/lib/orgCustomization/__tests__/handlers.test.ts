/**
 * @jest-environment node
 *
 * MED-454: MSW handler tests — prove GET /api/org-customization/registry/
 * and /effective/ resolve to the fixtures, including the ?fixture= switch.
 */
import { setupServer } from 'msw/node';
import {
  orgCustomizationHandlers,
} from '../../../../public/msw/orgCustomization.handlers';
import {
  allEnabledFixture,
  projectOverrideFixture,
  registryFixture,
} from '../../../../public/msw/orgCustomization.fixtures';
import { isEffectiveConfig } from '@/types/orgCustomization';

const server = setupServer(...orgCustomizationHandlers);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('GET /api/org-customization/registry/', () => {
  it('returns the registry fixture', async () => {
    const response = await fetch('https://app.test/api/org-customization/registry/');
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(registryFixture);
  });

  it('matches regardless of origin and trailing query', async () => {
    const response = await fetch('http://localhost:3000/api/org-customization/registry/?x=1');
    expect(response.status).toBe(200);
    expect((await response.json()).modules).toHaveLength(23);
  });
});

describe('GET /api/org-customization/effective/', () => {
  it('defaults to the all-enabled fixture', async () => {
    const response = await fetch('https://app.test/api/org-customization/effective/');
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toEqual(allEnabledFixture);
    expect(isEffectiveConfig(body)).toBe(true);
  });

  it('selects the project-override fixture via ?fixture=', async () => {
    const response = await fetch(
      'https://app.test/api/org-customization/effective/?fixture=project-override&project=42'
    );
    const body = await response.json();
    expect(body).toEqual(projectOverrideFixture);
    expect(body.version).toBe(2);
  });

  it('falls back to all-enabled for an unknown fixture name', async () => {
    const response = await fetch(
      'https://app.test/api/org-customization/effective/?fixture=nope'
    );
    expect(await response.json()).toEqual(allEnabledFixture);
  });
});
