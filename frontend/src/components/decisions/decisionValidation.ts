import { isAxiosError } from 'axios';
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

export function readDraftFieldError(
  error: unknown,
  field: string,
): string | undefined {
  if (!isAxiosError<Record<string, unknown>>(error)) return undefined;
  if (error.response?.status !== 400) return undefined;

  const value = error.response.data?.[field];
  const message = Array.isArray(value) ? value[0] : value;

  return typeof message === 'string' && message.trim()
    ? message
    : undefined;
}

export function validateDraftTitle(title: string): string | undefined {
  if (Array.from(title).length > 255) {
    return 'Title must be 255 characters or fewer.';
  }

  return undefined;
}

export function validateDraftConfidence(score: number | null): string | undefined {
  if (score !== null && (!Number.isInteger(score) || score < 1 || score > 5)) {
    return 'Confidence score must be an integer between 1 and 5.';
  }

  return undefined;
}

export function validateDraftOptions(options: DecisionOptionDraft[]): string | undefined {
  for (const [index, option] of options.entries()) {
    const label = `Option ${index + 1}`;
    if (!option.text.trim()) return `${label}: enter option text.`;
    if (Array.from(option.text).length > 1000) {
      return `${label}: use 1000 characters or fewer.`;
    }
  }

  return undefined;
}
