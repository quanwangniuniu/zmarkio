const PAGE_TEXT_START = 'Page text:\n---\n';
const PAGE_TEXT_END = '\n---\n\nInstruction:';
export const RESERVED_OUTPUT_TOKENS = 1024;
const MIN_CONTEXT_SAFETY_TOKENS = 64;

const AD_RELEVANCE_PATTERN = /\b(product|headline|description|primary text|body copy|ad copy|advertiser|offer|discount|sale|price|benefit|feature|shop now|buy now|learn more|call to action|cta|limited time|free shipping|delivery|save|subscribe|campaign|sponsored|try now|order now|new arrival|bestseller|made with|ingredients|specifications|product details|promo|promotion|deal|coupon|warranty|review|rating|from \$|only \$|% off)\b|(?:价格|优惠|折扣|促销|产品|卖点|立即购买|了解更多|免费配送|包邮|限时|活动|广告|标题|描述|特性|优势|成分|保修|评价|新款|购买|购买理由|价格|赠品|优惠券|新客|折合|税込|送料無料|割引|商品|特典|Comprar|preço|oferta|desconto|frete grátis|benefício|promoção)/i;
const BOILERPLATE_PATTERN = /\b(cookie preferences?|privacy policy|terms (?:of use|and conditions)|all rights reserved|skip to content|main navigation|footer|accessibility statement|sign in|log in|accept cookies|manage cookies|newsletter sign.?up)\b|(?:隐私政策|使用条款|Cookie 设置|登录|注册|网站导航)/i;
const PRICE_OR_OFFER_PATTERN = /(?:\$\s?\d|€\s?\d|£\s?\d|\b\d{1,3}%\s?(?:off|discount|折扣)?\b|\b(?:save|was|now|from)\s+\$?\d)/i;
const CTA_PATTERN = /\b(shop now|buy now|learn more|sign up|book now|get started|order now|subscribe|download|contact us|find out more|claim offer|add to cart)\b|(?:立即购买|马上购买|立即咨询|了解更多|马上订购|立即注册)/i;

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
  if (AD_RELEVANCE_PATTERN.test(value)) score += 8;
  if (PRICE_OR_OFFER_PATTERN.test(value)) score += 4;
  if (CTA_PATTERN.test(value)) score += 5;
  if (/^\s*(?:#{1,6}\s|h[1-6][.:\s]|title\s*:|headline\s*:|description\s*:)/i.test(value)) score += 3;
  if (BOILERPLATE_PATTERN.test(value)) score -= 8;
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
