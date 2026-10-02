import { buildJsonShapePrompt } from '@/src/ai/prompts';

describe('buildJsonShapePrompt', () => {
  it('uses non-Meta field names and their count', () => {
    expect(buildJsonShapePrompt(['headlines', 'descriptions'])).toBe(
      'OUTPUT JSON SHAPE\n'
      + 'Return strict JSON with exactly these two keys: headlines, descriptions. '
      + 'No prose, no explanation, no fences.\n\n'
    );
  });

  it('preserves the supplied field order', () => {
    expect(buildJsonShapePrompt(['body', 'title', 'action'])).toBe(
      'OUTPUT JSON SHAPE\n'
      + 'Return strict JSON with exactly these three keys: body, title, action. '
      + 'No prose, no explanation, no fences.\n\n'
    );
  });

  it.each<[number, string]>([
    [10, 'OUTPUT JSON SHAPE\n'
      + 'Return strict JSON with exactly these ten keys: field_1, field_2, '
      + 'field_3, field_4, field_5, field_6, field_7, field_8, field_9, field_10. '
      + 'No prose, no explanation, no fences.\n\n'],
    [11, 'OUTPUT JSON SHAPE\n'
      + 'Return strict JSON with exactly these 11 keys: field_1, field_2, '
      + 'field_3, field_4, field_5, field_6, field_7, field_8, field_9, field_10, field_11. '
      + 'No prose, no explanation, no fences.\n\n'],
  ])('formats the count for %i fields', (count, expected) => {
    const fieldKeys = Array.from({ length: count }, (_, index) => `field_${index + 1}`);
    expect(buildJsonShapePrompt(fieldKeys)).toBe(expected);
  });
});
