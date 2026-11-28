import { buildExternalUrlPrompt, buildSystemPrompt, buildUserPrompt } from '@/src/ai/prompts';
import { metaSpec } from '@/src/platforms/meta';
import type { PlatformSpec } from '@/src/platforms/types';

const freeTextSpec: PlatformSpec = {
  id: 'test_free_text',
  displayName: 'Test free-text platform',
  fields: [
    { key: 'title', label: 'Title', type: 'string', required: true, limits: {} },
    { key: 'body', label: 'Body', type: 'string', required: true, limits: {} },
    { key: 'action', label: 'Action', type: 'string', required: true, limits: {} },
  ],
  cta: { kind: 'freeText', field: 'action' },
  aspectRatios: [],
  language: { mode: 'source', excludedFields: [] },
  promptIntro: 'You write product copy for a test platform.\n\n',
  promptFragment: 'Use a concise, conversational tone.\n\n',
  promptVersion: 'test-v1',
  slugSource: { field: 'title' },
};

const source = {
  title: 'Plan a better weekend',
  body: 'Pack comfortable essentials for your next trip.',
  action: 'Explore the collection',
};
const pageText = `${source.title}\n${source.body}\n${source.action}`;

describe('platform-aware prompts', () => {
  it('assembles the free-text platform system prompt', () => {
    expect(buildSystemPrompt(freeTextSpec)).toMatchSnapshot();
  });

  it('builds the free-text platform template user prompt', () => {
    expect(buildUserPrompt(source, '', freeTextSpec)).toMatchSnapshot();
  });

  it('reads an existing Meta creative by its own fields while requesting the target fields', () => {
    const template = {
      hook: 'Travel light', headline: 'Weekend essentials', description: 'Pack one bag.', cta: 'SHOP_NOW',
    };
    const prompt = buildUserPrompt(template, '', freeTextSpec, metaSpec.fields);

    expect(prompt).toContain([
      '- Hook: Travel light', '- Headline: Weekend essentials', '- Description: Pack one bag.', '- CTA: SHOP_NOW',
    ].join('\n'));
    expect(prompt).toContain('The title, body and action must each differ from the source.');
    expect(prompt).not.toContain('undefined');
  });

  it('allows optional fields to be omitted in the system, template and URL prompts', () => {
    const spec = {
      ...freeTextSpec,
      fields: freeTextSpec.fields.map(field => field.key === 'body' ? { ...field, required: false } : field),
    };
    const keyRule = 'using only these keys: title, body, action. Optional keys: body. All other keys are required.';
    const templatePrompt = buildUserPrompt({ title: source.title, action: source.action }, '', spec);
    const urlPrompt = buildExternalUrlPrompt(pageText, '', spec);

    expect(buildSystemPrompt(spec)).toContain(keyRule);
    expect(templatePrompt).toContain('Rewrite each field you return');
    expect(templatePrompt).toContain('The title and action must each differ from the source.');
    expect(templatePrompt).not.toContain('undefined');
    expect(urlPrompt).toContain(keyRule);
    expect(urlPrompt).toContain('Rewrite each field you return');
  });

  it('builds the free-text platform external URL user prompt', () => {
    expect(buildExternalUrlPrompt(pageText, '', freeTextSpec)).toMatchSnapshot();
  });

  it('locks an exact CTA enum without requiring English uppercase', () => {
    const exactSpec: PlatformSpec = {
      ...freeTextSpec,
      cta: {
        kind: 'enum', field: 'action', values: ['Learn more', 'Shop now'],
        normalization: 'exact', fallback: 'Learn more',
      },
      // CTA casing follows enum normalization even when the field is excluded from translation.
      language: { mode: 'source', excludedFields: ['action'] },
    };
    const userPrompt = buildUserPrompt(source, '', exactSpec);
    const urlPrompt = buildExternalUrlPrompt(pageText, '', exactSpec);

    expect(buildSystemPrompt(exactSpec)).not.toContain('English uppercase');
    expect(userPrompt).toContain('the action enum lock');
    expect(userPrompt).toContain('The title and body must each differ from the source.');
    expect(urlPrompt).toContain('Apply all length caps, the action enum lock, and the diversity rule');
    expect(urlPrompt).not.toContain('English uppercase');
  });
});
