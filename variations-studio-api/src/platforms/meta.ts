import { buildJsonShapePrompt, PROMPT_VERSION, SHARED_SYSTEM_RULES } from '@/src/ai/prompts';
import type { PlatformSpec } from './types';

const CTA_ENUM_ALLOWLIST = (
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

const fields = [
  { key: 'hook', label: 'Hook', type: 'string', required: true,
    limits: { maxWords: 10, maxChars: 50 } },
  { key: 'headline', label: 'Headline', type: 'string', required: true,
    limits: { maxChars: 40 } },
  { key: 'description', label: 'Description', type: 'string', required: true,
    limits: { maxChars: 125 } },
  { key: 'cta', label: 'CTA', type: 'string', required: true, limits: {} },
] as const;

const promptFragment = (
  'You are an expert paid-media copywriter producing high-conversion ad copy '
  + 'variations for Meta (Facebook + Instagram Feed). Your output is consumed '
  + 'directly by Meta\'s Marketing API, so format and length constraints are hard '
  + 'rules, not stylistic preferences.\n\n'
  + buildJsonShapePrompt(fields.map((field) => field.key))
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
  + SHARED_SYSTEM_RULES
);

export const metaSpec = {
  id: 'meta',
  displayName: 'Meta (Facebook + Instagram Feed)',
  fields,
  cta: {
    kind: 'enum',
    field: 'cta',
    values: CTA_ENUM_ALLOWLIST.split(',').map((value) => value.trim()),
    normalization: 'trim-uppercase-underscores',
    fallback: 'SHOP_NOW',
  },
  // Feed creative presets.
  aspectRatios: ['1:1', '4:5'],
  language: { mode: 'source', excludedFields: ['cta'] },
  // Full system prompt: the Meta sections plus the shared rules.
  promptFragment,
  promptVersion: PROMPT_VERSION,
  slugSource: { field: 'headline' },
} as const satisfies PlatformSpec;
