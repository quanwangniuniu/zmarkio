export const MAX_BATCH = 50;
export const BATCH_CONCURRENCY = 5;
export const MODEL_NAME = 'gemini-2.5-flash-lite';
export const PROMPT_VERSION = 'v1';
export const AI_QUOTA_MESSAGE =
  'AI generation is temporarily rate-limited or quota-limited. Please wait '
  + 'a minute before generating more variations, or reduce the number of '
  + 'variations and try again.';

export const CTA_ENUM_ALLOWLIST = (
  'OPEN_LINK, LIKE_PAGE, SHOP_NOW, PLAY_GAME, INSTALL_APP, USE_APP, CALL, '
  + 'CALL_ME, VIDEO_CALL, INSTALL_MOBILE_APP, USE_MOBILE_APP, MOBILE_DOWNLOAD, '
  + 'BOOK_TRAVEL, LISTEN_MUSIC, WATCH_VIDEO, LEARN_MORE, SIGN_UP, DOWNLOAD, '
  + 'WATCH_MORE, NO_BUTTON, VISIT_PAGES_FEED, CALL_NOW, APPLY_NOW, CONTACT, '
  + 'BUY_NOW, GET_OFFER, GET_OFFER_VIEW, BUY_TICKETS, UPDATE_APP, '
  + 'GET_DIRECTIONS, BUY, SEND_UPDATES, MESSAGE_PAGE, DONATE, SUBSCRIBE, '
  + 'SAY_THANKS, SELL_NOW, SHARE, DONATE_NOW, GET_QUOTE, CONTACT_US, '
  + 'ORDER_NOW, START_ORDER, ADD_TO_CART, VIEW_CART, VIEW_IN_CART, '
  + 'RECORD_NOW, INQUIRE_NOW, CONFIRM, REFER_FRIENDS, REQUEST_TIME, '
  + 'GET_SHOWTIMES, LISTEN_NOW, TRY_DEMO, FOLLOW_USER, RAISE_MONEY, SEE_SHOP, '
  + 'GET_DETAILS, FIND_OUT_MORE, VISIT_WEBSITE, BROWSE_SHOP, EVENT_RSVP, '
  + 'WHATSAPP_MESSAGE, SEE_MORE, BOOK_NOW, FIND_A_GROUP, FIND_YOUR_GROUPS, '
  + 'PAY_TO_ACCESS, PURCHASE_GIFT_CARDS, FOLLOW_PAGE, SEND_A_GIFT, '
  + 'SWIPE_UP_SHOP, SWIPE_UP_PRODUCT, SEND_GIFT_MONEY, GET_STARTED, '
  + 'AUDIO_CALL, GET_PROMOTIONS, JOIN_CHANNEL, MAKE_AN_APPOINTMENT, '
  + 'ASK_ABOUT_SERVICES, BOOK_A_CONSULTATION, GET_A_QUOTE, BUY_VIA_MESSAGE, '
  + 'ASK_FOR_MORE_INFO, CHAT_WITH_US, VIEW_PRODUCT, VIEW_CHANNEL, '
  + 'GET_IN_TOUCH, ASK_A_QUESTION, START_A_CHAT, CHAT_NOW, ASK_US, '
  + 'WATCH_LIVE_VIDEO, SHOP_WITH_AI, TRY_ON_WITH_AI'
);

export const CTA_ENUM = new Set(
  CTA_ENUM_ALLOWLIST.split(',').map((item) => item.trim()).filter(Boolean)
);

export const SYSTEM_PROMPT = (
  'You are an expert paid-media copywriter producing high-conversion ad copy '
  + 'variations for Meta (Facebook + Instagram Feed). Your output is consumed '
  + 'directly by Meta\'s Marketing API, so format and length constraints are hard '
  + 'rules, not stylistic preferences.\n\n'
  + 'OUTPUT JSON SHAPE\n'
  + 'Return strict JSON with exactly these four keys: hook, headline, '
  + 'description, cta. No prose, no explanation, no fences.\n\n'
  + 'LENGTH CAPS (HARD)\n'
  + '- hook: at most 10 words AND at most 50 characters. The hook is the first '
  + 'punchy line that stops the scroll.\n'
  + '- headline: at most 40 characters. Single line. No trailing punctuation.\n'
  + '- description: at most 125 characters. This maps to Meta\'s primary text. '
  + 'Aim for clarity over cleverness; Meta truncates beyond 125.\n'
  + 'If a field would naturally exceed its cap, REWRITE it shorter. Do not copy '
  + 'source length; the cap overrides the source.\n\n'
  + 'CALL-TO-ACTION (HARD)\n'
  + 'The cta value MUST be EXACTLY one of these enum strings, byte-for-byte '
  + `(uppercase, underscores, no spaces): ${CTA_ENUM_ALLOWLIST}.\n`
  + 'Do NOT translate the cta. Do NOT rephrase it as Title Case or sentence '
  + 'case. Do NOT invent values outside this enum. If the source\'s cta is '
  + 'already a valid enum, keep it; if the source\'s cta is free-text or '
  + 'non-English, map it to the closest enum value above.\n\n'
  + 'OUTPUT LANGUAGE\n'
  + 'Detect the language of the source ad copy. Output every text field in '
  + 'THAT SAME LANGUAGE. If the source is English, output English. If the '
  + 'source is Chinese, output Chinese. If the source is Portuguese, output '
  + 'Portuguese. NEVER translate to a different language. The cta field is the '
  + 'only exception — it stays in the English uppercase enum format regardless '
  + 'of source language.\n\n'
  + 'DIVERSITY\n'
  + 'Each call should explore a different angle: a different value proposition, '
  + 'a different emotional hook, or a different sentence structure. Avoid '
  + 'producing variations that read as near-duplicates of the source or of an '
  + 'obvious literal rewrite. Surprise, contrast, urgency, social proof, and '
  + 'specific numbers are all valid angles to vary across calls.\n\n'
  + 'VOICE\n'
  + 'Preserve the source\'s offer, target audience, and tone. Do not invent '
  + 'product features, prices, or claims that are not implied by the source.'
);

export type { CopyJson } from '@/src/ai/types';
import type { CopyJson } from '@/src/ai/types';

export function buildExternalUrlPrompt(pageText: string, instruction: string): string {
  const focus = instruction.trim()
    || 'Rewrite all four fields with fresh phrasing, exploring a different angle than a literal rewrite. Preserve the source language. Respect the length caps and the cta enum lock.';
  return (
    'Below is the rendered text content of a public ad page. The page may '
    + 'contain navigation, ad library metadata, advertiser info, and unrelated '
    + 'boilerplate. Identify the actual ad copy inside it (typically: a short '
    + 'hook line, a headline, a body paragraph, and a call-to-action button '
    + 'label), then produce a NEW VARIATION of that ad copy following the '
    + "user's instruction.\n\n"
    + 'LANGUAGE LOCK (CRITICAL)\n'
    + 'Detect the language of the ad copy embedded in the page text below. '
    + 'Output every text field in THAT SAME LANGUAGE. NEVER drift to English '
    + 'unless the source ad copy is already English. If the page is in '
    + 'Portuguese, the output must be in Portuguese. The cta field stays in '
    + 'the English uppercase enum format regardless of source language.\n\n'
    + 'Apply all length caps, the cta enum lock, and the diversity rule from '
    + 'the system instructions to the new variation. Each value in the JSON '
    + 'must be the NEW VARIATION, not the extracted source.\n\n'
    + `Page text:\n---\n${pageText}\n---\n\n`
    + `Instruction: ${focus}\n\n`
    + 'Return strict JSON with keys: hook, headline, description, cta. '
    + 'No prose, no fences.'
  );
}

export function buildUserPrompt(template: CopyJson, instruction: string): string {
  const focus = instruction.trim()
    || 'Rewrite all four fields with fresh phrasing, exploring a different angle than a literal rewrite. Respect the length caps and the cta enum lock.';
  return (
    'Template ad copy:\n'
    + `- Hook: ${template.hook}\n`
    + `- Headline: ${template.headline}\n`
    + `- Description: ${template.description}\n`
    + `- CTA: ${template.cta}\n\n`
    + `Instruction: ${focus}\n\n`
    + 'Return JSON: {"hook": "...", "headline": "...", "description": "...", "cta": "..."}'
  );
}

export function lockCta(raw: string): string {
  const value = raw.trim().toUpperCase().replace(/\s+/g, '_');
  if (CTA_ENUM.has(raw.trim())) return raw.trim();
  if (CTA_ENUM.has(value)) return value;
  return 'SHOP_NOW';
}
