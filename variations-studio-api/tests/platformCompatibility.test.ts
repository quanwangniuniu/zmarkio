import {
  CTA_ENUM_ALLOWLIST,
  lockCta,
  PROMPT_VERSION,
  SYSTEM_PROMPT,
} from '@/src/ai/prompts';
import { metaSpec } from '@/src/platforms/meta';

describe('Meta compatibility', () => {
  it('reuses the live prompt and CTA values in their existing order', () => {
    expect(metaSpec.promptFragment).toBe(SYSTEM_PROMPT);
    expect(metaSpec.promptVersion).toBe(PROMPT_VERSION);
    expect(metaSpec.cta.values).toEqual(CTA_ENUM_ALLOWLIST.split(',').map((value) => value.trim()));
    expect(metaSpec.cta).toMatchObject({
      kind: 'enum', field: 'cta', normalization: 'trim-uppercase-underscores', fallback: 'SHOP_NOW',
    });
    for (const value of metaSpec.cta.values) expect(lockCta(value)).toBe(value);
  });

  it.each([
    [' \tlearn \n more\t ', 'LEARN_MORE'],
    [' no button ', 'NO_BUTTON'],
    [' \n\t ', 'SHOP_NOW'],
    ['not a known CTA', 'SHOP_NOW'],
  ])('keeps the existing CTA lock for %j', (raw, expected) => {
    expect(lockCta(raw)).toBe(expected);
  });
});
