import { createHash } from 'node:crypto';
import {
  buildExternalUrlPrompt,
  buildUserPrompt,
  CTA_ENUM_ALLOWLIST,
  lockCta,
  PROMPT_VERSION,
  SYSTEM_PROMPT,
} from '@/src/ai/prompts';
import { metaSpec } from '@/src/platforms/meta';

const sha256 = (text: string) => createHash('sha256').update(text).digest('hex');
const template = {
  hook: 'Fresh ideas',
  headline: 'Make every moment count',
  description: 'Explore new possibilities today.',
  cta: 'LEARN_MORE',
};
const pageText = 'Navigation\nFresh ideas\nMake every moment count\nExplore new possibilities today.\nLearn more';

describe('Meta compatibility', () => {
  it('reuses the live prompt and all 95 CTA values in their existing order', () => {
    expect(metaSpec.promptFragment).toBe(SYSTEM_PROMPT);
    expect(metaSpec.promptVersion).toBe(PROMPT_VERSION);
    expect(metaSpec.cta.values).toHaveLength(95);
    expect(metaSpec.cta.values).toEqual(CTA_ENUM_ALLOWLIST.split(',').map((value) => value.trim()));
    expect(metaSpec.cta).toMatchObject({
      kind: 'enum', field: 'cta', normalization: 'trim-uppercase-underscores', fallback: 'SHOP_NOW',
    });
    for (const value of metaSpec.cta.values) expect(lockCta(value)).toBe(value);
  });

  it.each([
    ['NO_BUTTON', 'NO_BUTTON'],
    ['  NO_BUTTON  ', 'NO_BUTTON'],
    [' no button ', 'NO_BUTTON'],
    [' \tlearn \n more\t ', 'LEARN_MORE'],
    ['shop_now', 'SHOP_NOW'],
    ['', 'SHOP_NOW'],
    [' \n\t ', 'SHOP_NOW'],
    ['Click here', 'SHOP_NOW'],
    ['not a known CTA', 'SHOP_NOW'],
  ])('keeps the existing CTA lock for %j', (raw, expected) => {
    expect(lockCta(raw)).toBe(expected);
  });

  // Captured from origin/prod-preview cf5e5a9ebf91640991a3fb325affeaf3b3d85249.
  // Byte-for-byte fixtures guard this abstraction-only change against prompt drift.
  it.each([
    ['system prompt', SYSTEM_PROMPT,
      '0a6141ed79be4f188cc3f0f0c2ba7446a6826e3eaa999eb70e6dddd811525d36'],
    ['CTA allowlist', CTA_ENUM_ALLOWLIST,
      'd5e1d1bd79e17cf4e836aec5327d8be44922d0f5c83f3f1d5343756f4c374568'],
    ['default user prompt', buildUserPrompt(template, ' \n\t '),
      '280a29bd112a56a4c2944f4ce77278ed59c9f4be739e841a77569988140b8b7d'],
    ['custom user prompt', buildUserPrompt(template, '  Focus on the benefits.  '),
      'c320fd48d3bd48ae5017af14a32144440a2648c277bf82131121c5a871c3a7b5'],
    ['default external URL prompt', buildExternalUrlPrompt(pageText, ' \n\t '),
      'a85ef3150469452a6209c07a3d326d51b7c88a3e52b380fedcf86acbd5c0640c'],
    ['custom external URL prompt', buildExternalUrlPrompt(pageText, '  Focus on the benefits.  '),
      '49bdda0b1421428b02e115e614f8874d4405709014d2f8fd011a1b81bbb66093'],
  ])('preserves the original %s', (_name, prompt, expected) => {
    expect(sha256(prompt)).toBe(expected);
  });
});
