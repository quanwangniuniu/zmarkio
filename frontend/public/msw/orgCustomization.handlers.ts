/**
 * MED-454: MSW handlers for the org-customization API mocks.
 *
 * Intercepts GET /api/org-customization/registry/ and
 * GET /api/org-customization/effective/ so frontend work (M3/M4) can run
 * under `npm run dev` without the backend API. RegExp predicates match
 * regardless of origin (dev server, Storybook, test runner).
 *
 * The effective endpoint accepts a `?fixture=<name>` query param to select
 * the scenario: `all-enabled` (default), `partially-disabled`,
 * `project-override`.
 */
import { http, HttpResponse } from 'msw';
import {
  EFFECTIVE_FIXTURES,
  allEnabledFixture,
  registryFixture,
  type EffectiveFixtureName,
} from './orgCustomization.fixtures';

const orgCustomizationPath = (path: string) =>
  new RegExp(`^https?://[^/]+/api/org-customization${path}(\\?.*)?$`);

export const orgCustomizationHandlers = [
  http.get(orgCustomizationPath('/registry/'), () => HttpResponse.json(registryFixture)),
  http.get(orgCustomizationPath('/effective/'), ({ request }) => {
    const name = new URL(request.url).searchParams.get('fixture') ?? 'all-enabled';
    const fixture = EFFECTIVE_FIXTURES[name as EffectiveFixtureName] ?? allEnabledFixture;
    return HttpResponse.json(fixture);
  }),
];
