import { http, HttpResponse } from 'msw';
import { EFFECTIVE_FIXTURES, registryFixture, type EffectiveFixtureName } from './fixtures';

const isFixtureName = (value: string | null): value is EffectiveFixtureName =>
  value !== null && value in EFFECTIVE_FIXTURES;

export const orgCustomizationHandlers = [
  http.get('*/api/org-customization/registry/', () => HttpResponse.json(registryFixture)),
  http.get('*/api/org-customization/effective/', ({ request }) => {
    const requested = new URL(request.url).searchParams.get('fixture');
    return HttpResponse.json(EFFECTIVE_FIXTURES[isFixtureName(requested) ? requested : 'all_enabled']);
  }),
];
