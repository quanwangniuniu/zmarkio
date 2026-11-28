import type { DecisionOptionDraft } from '@/types/decision';
import { isOptionTextBlank } from '@/components/decisions/decisionValidation';

export interface OptionRow {
  key: string;
  text: string;
  isSelected: boolean;
  /** Last saved text; null if never saved. */
  savedText: string | null;
}

let lastKey = 0;
const nextRowKey = () => `option-${++lastKey}`;

const byOrder = (a: DecisionOptionDraft, b: DecisionOptionDraft) => (a.order ?? 0) - (b.order ?? 0);

export function rowsFromOptions(options: DecisionOptionDraft[] | null | undefined): OptionRow[] {
  return [...(options ?? [])].sort(byOrder).map((opt) => ({
    key: nextRowKey(),
    text: opt.text ?? '',
    isSelected: !!opt.isSelected,
    savedText: opt.text ?? '',
  }));
}

export function newOptionRow(): OptionRow {
  return { key: nextRowKey(), text: '', isSelected: false, savedText: null };
}

export interface OptionsSave {
  options: DecisionOptionDraft[];
  sent: Map<string, string>;
}

// Never sends blank text: unsaved blank rows are skipped,
// cleared saved rows resend their last saved text.
export function buildOptionsSave(rows: OptionRow[]): OptionsSave {
  const options: DecisionOptionDraft[] = [];
  const sent = new Map<string, string>();
  for (const row of rows) {
    let text = row.text;
    if (isOptionTextBlank(text)) {
      if (isOptionTextBlank(row.savedText)) continue;
      text = row.savedText as string;
    }
    options.push({ text, isSelected: row.isSelected, order: options.length });
    sent.set(row.key, text);
  }
  return { options, sent };
}

export function applySaved(rows: OptionRow[], sent: Map<string, string>): OptionRow[] {
  return rows.map((row) => (sent.has(row.key) ? { ...row, savedText: sent.get(row.key) as string } : row));
}

/** Ignores ids and timestamps, so a re-fetched copy counts as unchanged. */
export function optionsKey(options: DecisionOptionDraft[] | null | undefined): string {
  return JSON.stringify(
    [...(options ?? [])].sort(byOrder).map((opt) => [opt.text ?? '', !!opt.isSelected, opt.order ?? 0]),
  );
}
