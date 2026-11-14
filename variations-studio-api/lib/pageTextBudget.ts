const PAGE_TEXT_START = 'Page text:\n---\n';
const PAGE_TEXT_END = '\n---\n\nInstruction:';
export const RESERVED_OUTPUT_TOKENS = 1024;
const MIN_CONTEXT_SAFETY_TOKENS = 64;

// Language-specific ad relevance bonuses are English-only. Add ordinary
// phrases to these lists, and keep punctuation/optional syntax in the
// special-case regex lists directly below them.
const AD_RELEVANCE_TERMS = [
  'product',
  'headline',
  'description',
  'primary text',
  'body copy',
  'ad copy',
  'advertiser',
  'offer',
  'discount',
  'sale',
  'price',
  'benefit',
  'feature',
  'shop now',
  'buy now',
  'learn more',
  'call to action',
  'cta',
  'limited time',
  'free shipping',
  'delivery',
  'save',
  'subscribe',
  'campaign',
  'sponsored',
  'try now',
  'order now',
  'new arrival',
  'bestseller',
  'made with',
  'ingredients',
  'specifications',
  'product details',
  'promo',
  'promotion',
  'deal',
  'coupon',
  'warranty',
  'review',
  'rating',
] as const;
const AD_RELEVANCE_SPECIAL_CASE_PATTERNS = [
  /\bfrom \$\b/i,
  /\bonly \$\b/i,
  /\b% off\b/i,
];

const BOILERPLATE_TERMS = [
  'privacy policy',
  'all rights reserved',
  'skip to content',
  'main navigation',
  'footer',
  'accessibility statement',
  'sign in',
  'log in',
  'accept cookies',
  'manage cookies',
] as const;
const BOILERPLATE_SPECIAL_CASE_PATTERNS = [
  /\bcookie preferences?\b/i,
  /\bterms (?:of use|and conditions)\b/i,
  /\bnewsletter sign.?up\b/i,
];

const PRICE_OR_OFFER_PATTERNS = [
  /(?:\$\s?\d|€\s?\d|£\s?\d)/i,
  /\b\d{1,3}%\s?(?:off|discount)?\b/i,
  /\b(?:save|was|now|from)\s+\$?\d/i,
];

const CTA_TERMS = [
  'shop now',
  'buy now',
  'learn more',
  'sign up',
  'book now',
  'get started',
  'order now',
  'subscribe',
  'download',
  'contact us',
  'find out more',
  'claim offer',
  'add to cart',
] as const;

const AD_RELEVANCE_PATTERN = buildPhrasePattern(AD_RELEVANCE_TERMS);
const BOILERPLATE_PATTERN = buildPhrasePattern(BOILERPLATE_TERMS);
const CTA_PATTERN = buildPhrasePattern(CTA_TERMS);
const HEADING_PATTERN = /^\s*(?:#{1,6}\s|h[1-6][.:\s]|title\s*:|headline\s*:|description\s*:)/i;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function buildPhrasePattern(phrases: readonly string[]): RegExp {
  const alternatives = phrases.map(escapeRegExp).join('|');
  return new RegExp(`\\b(?:${alternatives})\\b`, 'i');
}

function matchesAnyPattern(value: string, patterns: readonly RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(value));
}

type ScoredLine = { line: string; index: number; score: number };
type CostFunction = (value: string) => number;

/**
 * Conservative approximate token count. ASCII text is budgeted at roughly
 * three characters per token with a small word-boundary allowance; non-ASCII
 * code points are budgeted individually to avoid under-counting CJK copy.
 */
export function estimateTokenCount(text: string): number {
  if (!text) return 0;
  const points = Array.from(text);
  let nonAscii = 0;
  for (const point of points) {
    if ((point.codePointAt(0) ?? 0) > 0x7f) nonAscii += 1;
  }
  const asciiChars = points.length - nonAscii;
  const asciiWords = (text.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g) ?? []).length;
  return Math.ceil(asciiChars / 3 + asciiWords * 0.2 + nonAscii * 1.1);
}

function scoreLine(line: string, index: number): number {
  const value = line.trim();
  if (!value) return -100;
  let score = value.length >= 20 && value.length <= 500 ? 2 : 0;
  if (index < 8) score += 1;
  if (
    AD_RELEVANCE_PATTERN.test(value)
    || matchesAnyPattern(value, AD_RELEVANCE_SPECIAL_CASE_PATTERNS)
  ) {
    score += 8;
  }
  if (matchesAnyPattern(value, PRICE_OR_OFFER_PATTERNS)) score += 4;
  if (CTA_PATTERN.test(value)) score += 5;
  if (HEADING_PATTERN.test(value)) score += 3;
  if (
    BOILERPLATE_PATTERN.test(value)
    || matchesAnyPattern(value, BOILERPLATE_SPECIAL_CASE_PATTERNS)
  ) {
    score -= 8;
  }
  if (value.length > 1200) score -= 2;
  return score;
}

function prefixWithinBudget(value: string, maxBudget: number, cost: CostFunction): string {
  let low = 0;
  let high = value.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (cost(value.slice(0, middle)) <= maxBudget) low = middle;
    else high = middle - 1;
  }
  return value.slice(0, low).trimEnd();
}

function selectRelevantLines(text: string, maxBudget: number, cost: CostFunction): string {
  if (!text || maxBudget <= 0) return '';

  const lines: ScoredLine[] = text.split(/\r?\n/).map((line, index) => ({
    line,
    index,
    score: scoreLine(line, index),
  })).filter(({ line }) => line.trim().length > 0);

  if (lines.length === 0) return '';

  const ranked = [...lines].sort((a, b) => b.score - a.score || a.index - b.index);
  const selected: ScoredLine[] = [];
  let used = 0;
  const separatorCost = cost('\n');

  for (const item of ranked) {
    const lineCost = cost(item.line);
    const extraCost = selected.length > 0 ? separatorCost : 0;
    if (used + extraCost + lineCost <= maxBudget) {
      selected.push(item);
      used += extraCost + lineCost;
      continue;
    }

    // If the highest-ranked line itself is too long, keep a safe prefix of it.
    // Otherwise, skip it and allow shorter high-value lines to fit instead.
    if (selected.length === 0) {
      const partial = prefixWithinBudget(item.line, maxBudget, cost);
      if (partial) {
        selected.push({ ...item, line: partial });
        used = cost(partial);
      }
    }
  }

  // Use any remaining budget for a prefix of the next-ranked line instead of
  // leaving most of the context unused when one paragraph is very long.
  if (used < maxBudget) {
    const selectedIndexes = new Set(selected.map(({ index }) => index));
    for (const item of ranked) {
      if (selectedIndexes.has(item.index)) continue;
      const remainingBudget = maxBudget - used - (selected.length > 0 ? separatorCost : 0);
      const partial = prefixWithinBudget(item.line, remainingBudget, cost);
      if (partial && partial.length < item.line.length) {
        selected.push({ ...item, line: partial });
        break;
      }
    }
  }

  if (selected.length === 0) {
    return prefixWithinBudget(lines[0].line, maxBudget, cost);
  }

  return selected.sort((a, b) => a.index - b.index).map(({ line }) => line).join('\n');
}

/** Select ad-relevant lines within an extraction-time character cap. */
export function truncateRelevantTextByCharacters(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  return selectRelevantLines(text, maxChars, (value) => value.length);
}

/** Select ad-relevant lines within an approximate token budget. */
export function truncateRelevantTextByTokens(text: string, maxTokens: number): string {
  return selectRelevantLines(text, maxTokens, estimateTokenCount);
}

/**
 * If the prompt contains the External URL page-text section, shrink only that
 * section until the system prompt, remaining user prompt and reserved output
 * fit within the configured context window. Other generation modes are untouched.
 */
export function fitExternalUrlPromptToContext(
  systemPrompt: string,
  userPrompt: string,
  numCtx: number,
  reservedOutputTokens = RESERVED_OUTPUT_TOKENS,
): string {
  const markerStart = userPrompt.indexOf(PAGE_TEXT_START);
  if (markerStart < 0) return userPrompt;

  const pageStart = markerStart + PAGE_TEXT_START.length;
  const pageEnd = userPrompt.indexOf(PAGE_TEXT_END, pageStart);
  if (pageEnd < 0) return userPrompt;

  const pageText = userPrompt.slice(pageStart, pageEnd);
  const prefix = userPrompt.slice(0, pageStart);
  const suffix = userPrompt.slice(pageEnd);
  const fixedUserPrompt = prefix + suffix;
  const safetyTokens = Math.max(MIN_CONTEXT_SAFETY_TOKENS, Math.ceil(numCtx * 0.02));
  const fixedTokens = estimateTokenCount(systemPrompt) + estimateTokenCount(fixedUserPrompt);
  const pageTokenBudget = Math.max(
    0,
    Math.floor(numCtx - reservedOutputTokens - safetyTokens - fixedTokens),
  );
  const budgetedPageText = truncateRelevantTextByTokens(pageText, pageTokenBudget);

  return prefix + budgetedPageText + suffix;
}
