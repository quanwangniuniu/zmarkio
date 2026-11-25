import type { DecisionOptionDraft, DecisionRiskLevel } from '@/types/decision';

const FIELD_ORDER = [
  'title',
  'contextSummary',
  'signals',
  'options',
  'selectedOption',
  'reasoning',
  'riskLevel',
  'confidenceScore',
] as const;

const FIELD_ID_MAP: Record<string, string> = {
  title: 'decision-field-title',
  contextSummary: 'decision-field-contextSummary',
  signals: 'decision-field-signals',
  options: 'decision-field-options',
  selectedOption: 'decision-field-options',
  reasoning: 'decision-field-reasoning',
  riskLevel: 'decision-field-riskConfidence',
  confidenceScore: 'decision-field-riskConfidence',
};

export function scrollToFirstError(errors: Record<string, string>): void {
  const firstKey = FIELD_ORDER.find((key) => Boolean(errors[key]));
  if (!firstKey) return;
  const id = FIELD_ID_MAP[firstKey] ?? null;
  if (!id) return;
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export interface DecisionDraftFields {
  title: string;
  contextSummary: string;
  reasoning: string;
  riskLevel: DecisionRiskLevel | null;
  confidenceScore: number;
  options: DecisionOptionDraft[];
}

export function validateDecisionDraft(
  fields: DecisionDraftFields,
  signalCount: number,
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!fields.title.trim()) errors.title = 'Title is required.';
  if (!fields.contextSummary.trim()) errors.contextSummary = 'Context summary is required.';
  if (signalCount <= 0) errors.signals = 'At least one signal is required.';
  if (!fields.reasoning.trim()) errors.reasoning = 'Reasoning is required.';
  const nonEmptyOptions = fields.options.filter((o) => (o.text || '').trim().length > 0);
  if (nonEmptyOptions.length < 2) errors.options = 'At least two non-empty options are required.';
  const selectedCount = fields.options.filter((o) => o.isSelected).length;
  if (selectedCount !== 1) errors.selectedOption = 'Select exactly one option.';
  if (!fields.riskLevel) errors.riskLevel = 'Risk level is required.';
  if (!(fields.confidenceScore >= 1 && fields.confidenceScore <= 5))
    errors.confidenceScore = 'Confidence must be between 1 and 5.';
  return errors;
}

// ---------------------------------------------------------------------------
// MED-545: field limits for draft editing (mirrors decision-service
// DecisionDraftValidation). Lengths are counted in code points, not UTF-16
// units, so an emoji counts as 1 — same as the backend's EnumerateRunes.
// Do not use the HTML maxLength attribute for these fields.
// ---------------------------------------------------------------------------

export const DECISION_LIMITS = {
  title: 255,
  optionText: 1000,
  topicName: 80,
  topicKey: 64,
} as const;

/** Character count at which the counter becomes visible. */
export const COUNTER_THRESHOLDS = {
  title: 230,
  optionText: 900,
} as const;

export function charCount(text: string | null | undefined): number {
  return Array.from(text ?? '').length;
}

export function clip(text: string, max: number): { text: string; cut: boolean } {
  const chars = Array.from(text);
  return chars.length > max
    ? { text: chars.slice(0, max).join(''), cut: true }
    : { text, cut: false };
}

export interface LengthState {
  count: number;
  max: number;
  showCounter: boolean;
  atLimit: boolean;
}

export function lengthState(text: string | null | undefined, max: number, showAt: number): LengthState {
  const count = charCount(text);
  return { count, max, showCounter: count >= showAt, atLimit: count >= max };
}

const formatCount = (n: number) => n.toLocaleString('en-US');

export const FIELD_MESSAGES = {
  limitReached: 'Character limit reached.',
  pastedCut: (max: number) => `Pasted text was cut to fit ${formatCount(max)} characters.`,
  optionEmpty: 'Enter text, or remove this option.',
  optionsTooFew: "Add at least two options, then choose the one you're committing to.",
  optionNotSelected: "Choose the option you're committing to.",
  confidenceOutOfRange: (value: number) => `Saved value ${value} is out of range. Choose 1–5.`,
  topicNameEmpty: 'Enter a topic name.',
  topicKeyEmpty: 'Include at least one letter or number (a–z, 0–9).',
  topicKeyTooLong: `This name is too long. Use ${DECISION_LIMITS.topicKey} characters or fewer.`,
  topicKeyExists: (key: string) => `A topic with the key ${key} already exists. Choose another name.`,
} as const;

export function formatCounter(state: LengthState): string {
  return `${formatCount(state.count)} / ${formatCount(state.max)}`;
}

export function isOptionTextBlank(text: string | null | undefined): boolean {
  return (text ?? '').trim().length === 0;
}

export function isConfidenceOutOfRange(value: number | null | undefined): value is number {
  return value != null && (value < 1 || value > 5);
}

// ---- Topic keys -----------------------------------------------------------

/** trim → lowercase → spaces/underscores to "-" → drop non [a-z0-9-] → collapse "-" → trim "-" */
export function toTopicKey(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[\s_]+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export interface TopicNameCheck {
  key: string;
  error: string | null;
}

export function validateNewTopicName(name: string, existingKeys: Iterable<string>): TopicNameCheck {
  if (!name.trim()) return { key: '', error: FIELD_MESSAGES.topicNameEmpty };
  const key = toTopicKey(name);
  if (!key) return { key, error: FIELD_MESSAGES.topicKeyEmpty };
  if (key.length > DECISION_LIMITS.topicKey) {
    return { key, error: FIELD_MESSAGES.topicKeyTooLong };
  }
  const existing = new Set(Array.from(existingKeys, (k) => k.trim().toLowerCase()));
  if (existing.has(key)) return { key, error: FIELD_MESSAGES.topicKeyExists(key) };
  return { key, error: null };
}

// ---- Server errors --------------------------------------------------------

const snakeToCamel = (field: string) => field.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());

/**
 * Turns a failed request into `{ field: message }`.
 * Supports decision-service 400s (`{ title: ["..."] }`) and the commit
 * format (`{ error: { details: { fieldErrors: [{ field, message }] } } }`).
 * snake_case field names are converted to camelCase.
 * Returns {} when the error carries no field errors (e.g. network failure).
 */
export function extractFieldErrors(err: unknown): Record<string, string> {
  const data = (err as { response?: { data?: unknown } })?.response?.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return {};

  const result: Record<string, string> = {};
  const nested = (data as { error?: { details?: { fieldErrors?: unknown } } }).error?.details?.fieldErrors;
  if (Array.isArray(nested)) {
    for (const item of nested) {
      const field = (item as { field?: unknown })?.field;
      const message = (item as { message?: unknown })?.message;
      if (typeof field === 'string' && typeof message === 'string') {
        result[snakeToCamel(field)] ??= message;
      }
    }
    return result;
  }

  for (const [field, value] of Object.entries(data as Record<string, unknown>)) {
    if (field === 'detail' || field === 'error' || field === 'message') continue;
    const message = Array.isArray(value) ? value.find((v) => typeof v === 'string') : value;
    if (typeof message === 'string') result[snakeToCamel(field)] = message;
  }
  return result;
}