import type { GenerationOutputKey } from '@/types/agent';

export const GENERATION_OUTPUT_STORAGE_KEY = 'agent-generation-outputs-default';

export const GENERATION_OUTPUT_CATALOG: {
  key: GenerationOutputKey;
  label: string;
  description: string;
}[] = [
  {
    key: 'recommended_tasks',
    label: 'Tasks',
    description: 'Recommended tasks from spreadsheet analysis.',
  },
  {
    key: 'recommended_decision_tree',
    label: 'Decision tree',
    description: 'Layered decision tree from spreadsheet analysis.',
  },
  {
    key: 'miro_board',
    label: 'Miro board',
    description: 'Generate a Miro board from analysis context.',
  },
];

export const DEFAULT_GENERATION_OUTPUTS: GenerationOutputKey[] =
  GENERATION_OUTPUT_CATALOG.map((item) => item.key);

export function effectiveGenerationOutputs(
  requested: GenerationOutputKey[]
): GenerationOutputKey[] {
  return requested.length > 0 ? requested : [...DEFAULT_GENERATION_OUTPUTS];
}

export function parseStoredGenerationOutputs(raw: string | null): GenerationOutputKey[] {
  if (!raw) return [...DEFAULT_GENERATION_OUTPUTS];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [...DEFAULT_GENERATION_OUTPUTS];
    const valid = parsed.filter(
      (k): k is GenerationOutputKey =>
        typeof k === 'string' &&
        GENERATION_OUTPUT_CATALOG.some((entry) => entry.key === k)
    );
    return valid.length > 0 ? valid : [...DEFAULT_GENERATION_OUTPUTS];
  } catch {
    return [...DEFAULT_GENERATION_OUTPUTS];
  }
}
