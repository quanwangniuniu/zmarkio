import { buildExternalUrlPrompt, buildSystemPrompt, buildUserPrompt } from '@/src/ai/prompts';
import { metaSpec } from '@/src/platforms/meta';

describe('Meta prompt compatibility', () => {
  it('preserves the system prompt', () => {
    expect(buildSystemPrompt(metaSpec)).toMatchSnapshot();
  });

  it('preserves the template user prompt', () => {
    expect(buildUserPrompt({
      hook: 'Make room for your next adventure',
      headline: 'Explore the weekend collection',
      description: 'Discover comfortable essentials for your next weekend away.',
      cta: 'SHOP_NOW',
    }, '', metaSpec)).toMatchSnapshot();
  });

  it('preserves the external URL user prompt', () => {
    expect(buildExternalUrlPrompt(
      'Weekend collection\nComfortable essentials for your next adventure.\nShop now',
      '',
      metaSpec,
    )).toMatchSnapshot();
  });
});
