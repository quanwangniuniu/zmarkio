import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from .ollama_client import BUSY_STATUSES, call_ollama_json, ollama_model
from .url_fetcher import fetch_url_text
from meta_ads.models import MetaAdCreative

logger = logging.getLogger(__name__)

MAX_BATCH = 50
BATCH_CONCURRENCY = 5
PROMPT_VERSION = 'v2'
AI_QUOTA_MESSAGE = (
    "AI generation is temporarily rate-limited or quota-limited. Please wait "
    "a minute before generating more variations, or reduce the number of "
    "variations and try again."
)


CTA_ENUM_ALLOWLIST = (
    "OPEN_LINK, LIKE_PAGE, SHOP_NOW, PLAY_GAME, INSTALL_APP, USE_APP, CALL, "
    "CALL_ME, VIDEO_CALL, INSTALL_MOBILE_APP, USE_MOBILE_APP, MOBILE_DOWNLOAD, "
    "BOOK_TRAVEL, LISTEN_MUSIC, WATCH_VIDEO, LEARN_MORE, SIGN_UP, DOWNLOAD, "
    "WATCH_MORE, NO_BUTTON, VISIT_PAGES_FEED, CALL_NOW, APPLY_NOW, CONTACT, "
    "BUY_NOW, GET_OFFER, GET_OFFER_VIEW, BUY_TICKETS, UPDATE_APP, "
    "GET_DIRECTIONS, BUY, SEND_UPDATES, MESSAGE_PAGE, DONATE, SUBSCRIBE, "
    "SAY_THANKS, SELL_NOW, SHARE, DONATE_NOW, GET_QUOTE, CONTACT_US, "
    "ORDER_NOW, START_ORDER, ADD_TO_CART, VIEW_CART, VIEW_IN_CART, "
    "RECORD_NOW, INQUIRE_NOW, CONFIRM, REFER_FRIENDS, REQUEST_TIME, "
    "GET_SHOWTIMES, LISTEN_NOW, TRY_DEMO, FOLLOW_USER, RAISE_MONEY, SEE_SHOP, "
    "GET_DETAILS, FIND_OUT_MORE, VISIT_WEBSITE, BROWSE_SHOP, EVENT_RSVP, "
    "WHATSAPP_MESSAGE, SEE_MORE, BOOK_NOW, FIND_A_GROUP, FIND_YOUR_GROUPS, "
    "PAY_TO_ACCESS, PURCHASE_GIFT_CARDS, FOLLOW_PAGE, SEND_A_GIFT, "
    "SWIPE_UP_SHOP, SWIPE_UP_PRODUCT, SEND_GIFT_MONEY, GET_STARTED, "
    "AUDIO_CALL, GET_PROMOTIONS, JOIN_CHANNEL, MAKE_AN_APPOINTMENT, "
    "ASK_ABOUT_SERVICES, BOOK_A_CONSULTATION, GET_A_QUOTE, BUY_VIA_MESSAGE, "
    "ASK_FOR_MORE_INFO, CHAT_WITH_US, VIEW_PRODUCT, VIEW_CHANNEL, "
    "GET_IN_TOUCH, ASK_A_QUESTION, START_A_CHAT, CHAT_NOW, ASK_US, "
    "WATCH_LIVE_VIDEO, SHOP_WITH_AI, TRY_ON_WITH_AI"
)


SYSTEM_PROMPT = (
    "You are an expert paid-media copywriter producing high-conversion ad copy "
    "variations for Meta (Facebook + Instagram Feed). Your output is consumed "
    "directly by Meta's Marketing API, so format and length constraints are hard "
    "rules, not stylistic preferences.\n\n"
    "OUTPUT JSON SHAPE\n"
    "Return strict JSON with exactly these four keys: hook, headline, "
    "description, cta. No prose, no explanation, no fences.\n\n"
    "LENGTH CAPS (HARD)\n"
    "- hook: at most 10 words AND at most 50 characters. The hook is the first "
    "punchy line that stops the scroll.\n"
    "- headline: at most 40 characters. Single line. No trailing punctuation.\n"
    "- description: at most 125 characters. This maps to Meta's primary text. "
    "Aim for clarity over cleverness; Meta truncates beyond 125.\n"
    "If a field would naturally exceed its cap, REWRITE it shorter. Do not copy "
    "source length; the cap overrides the source.\n\n"
    "CALL-TO-ACTION (HARD)\n"
    "The cta value MUST be EXACTLY one of these enum strings, byte-for-byte "
    f"(uppercase, underscores, no spaces): {CTA_ENUM_ALLOWLIST}.\n"
    "Do NOT translate the cta. Do NOT rephrase it as Title Case or sentence "
    "case. Do NOT invent values outside this enum. If the source's cta is "
    "already a valid enum, keep it; if the source's cta is free-text or "
    "non-English, map it to the closest enum value above.\n\n"
    "OUTPUT LANGUAGE\n"
    "Detect the language of the source ad copy. Output every text field in "
    "THAT SAME LANGUAGE. If the source is English, output English. If the "
    "source is Chinese, output Chinese. If the source is Portuguese, output "
    "Portuguese. NEVER translate to a different language. The cta field is the "
    "only exception — it stays in the English uppercase enum format regardless "
    "of source language.\n\n"
    "DIVERSITY\n"
    "Each call should explore a different angle: a different value proposition, "
    "a different emotional hook, or a different sentence structure. Avoid "
    "producing variations that read as near-duplicates of the source or of an "
    "obvious literal rewrite. Surprise, contrast, urgency, social proof, and "
    "specific numbers are all valid angles to vary across calls.\n\n"
    "VOICE\n"
    "Preserve the source's offer, target audience, and tone. Do not invent "
    "product features, prices, or claims that are not implied by the source."
)


def is_ai_quota_error(exc: Exception) -> bool:
    response = getattr(exc, 'response', None)
    status_code = getattr(response, 'status_code', None)
    return status_code in BUSY_STATUSES


def _build_user_prompt(template: dict, instruction: str) -> str:
    focus = instruction.strip() or "Rewrite all four fields with fresh phrasing, exploring a different angle than a literal rewrite. Respect the length caps and the cta enum lock."
    return (
        f"Source ad (reference only; every text field you return must use NEW wording):\n"
        f"- Hook: {template.get('hook', '')}\n"
        f"- Headline: {template.get('headline', '')}\n"
        f"- Description: {template.get('description', '')}\n"
        f"- CTA: {template.get('cta', '')}\n\n"
        f"Instruction: {focus}\n\n"
        f"Write one new variation of the source ad."
    )


# Angles rotated across a batch; each reshapes facts already in the source.
VARIATION_ANGLES = (
    "lead with the main benefit",
    "open with a short question about the problem",
    "lead with the offer already in the source ad",
    "describe how it feels to use the product",
)


def _with_angle(user_prompt: str, angle_index: int) -> str:
    return f"{user_prompt}\nAngle: {VARIATION_ANGLES[angle_index % len(VARIATION_ANGLES)]}."


def _is_source_copy(copy: dict, template: dict) -> bool:
    return all(
        str(copy.get(field, '')).strip() == str(template.get(field, '')).strip()
        for field in ('hook', 'headline', 'description')
    )


def _generate_new_copy(template: dict, instruction: str, angle_index: int) -> dict:
    user_prompt = _with_angle(_build_user_prompt(template, instruction), angle_index)
    copy = call_ollama_json(SYSTEM_PROMPT, user_prompt)
    # Small local models sometimes hand the source back unchanged; ask once more.
    if _is_source_copy(copy, template):
        copy = call_ollama_json(SYSTEM_PROMPT, user_prompt)
    return copy


def _creative_to_template(creative: MetaAdCreative) -> dict:
    body = creative.body or ''
    hook = body.split('\n', 1)[0] if body else ''
    return {
        'hook': hook,
        'headline': creative.title or '',
        'description': body,
        'cta': creative.call_to_action_type or '',
    }


def generate_from_existing(creative_id: int, instruction: str = '', angle_index: int = 0) -> dict:
    creative = MetaAdCreative.objects.get(pk=creative_id)
    template = _creative_to_template(creative)
    return _generate_new_copy(template, instruction, angle_index)


def generate_from_custom(base_copy: dict, instruction: str = '', angle_index: int = 0) -> dict:
    return _generate_new_copy(base_copy, instruction, angle_index)


EXTERNAL_URL_PROMPT_PREFIX = (
    "Below is the rendered text content of a public ad page. The page may "
    "contain navigation, ad library metadata, advertiser info, and unrelated "
    "boilerplate. Identify the actual ad copy inside it (typically: a short "
    "hook line, a headline, a body paragraph, and a call-to-action button "
    "label), then produce a NEW VARIATION of that ad copy following the "
    "user's instruction.\n\n"
    "LANGUAGE LOCK (CRITICAL)\n"
    "Detect the language of the ad copy embedded in the page text below. "
    "Output every text field in THAT SAME LANGUAGE. NEVER drift to English "
    "unless the source ad copy is already English. If the page is in "
    "Portuguese, the output must be in Portuguese. The cta field stays in "
    "the English uppercase enum format regardless of source language.\n\n"
    "Apply all length caps, the cta enum lock, and the diversity rule from "
    "the system instructions to the new variation. Each value in the JSON "
    "must be the NEW VARIATION, not the extracted source.\n\n"
    "Page text:\n---\n{page_text}\n---\n\n"
    "Instruction: {instruction}\n\n"
    "Return strict JSON with keys: hook, headline, description, cta. "
    "No prose, no fences."
)


def generate_from_external_url(url: str, instruction: str = '', angle_index: int = 0) -> dict:
    page_text = fetch_url_text(url)
    focus = instruction.strip() or "Rewrite all four fields with fresh phrasing, exploring a different angle than a literal rewrite. Preserve the source language. Respect the length caps and the cta enum lock."
    user_prompt = EXTERNAL_URL_PROMPT_PREFIX.format(
        page_text=page_text,
        instruction=focus,
    )
    return call_ollama_json(SYSTEM_PROMPT, _with_angle(user_prompt, angle_index))


def _single_generate_dispatch(
    source_mode: str,
    source_kwargs: dict,
    instruction: str,
    angle_index: int = 0,
) -> dict:
    if source_mode == 'existing':
        creative_id = source_kwargs.get('creative_id')
        if not creative_id:
            raise ValueError('creative_id required for source_mode=existing')
        return generate_from_existing(int(creative_id), instruction, angle_index)
    if source_mode == 'custom':
        base_copy = source_kwargs.get('base_copy') or {}
        return generate_from_custom(base_copy, instruction, angle_index)
    if source_mode == 'external_url':
        url = (source_kwargs.get('url') or '').strip()
        if not url:
            raise ValueError('url required for source_mode=external_url')
        return generate_from_external_url(url, instruction, angle_index)
    raise ValueError(f'unknown source_mode: {source_mode}')


def generate_batch(
    source_mode: str,
    count: int,
    source_kwargs: dict,
    instruction: str = '',
) -> dict:
    if count < 1 or count > MAX_BATCH:
        raise ValueError(f'count must be between 1 and {MAX_BATCH}')

    batch_id = str(uuid.uuid4())
    results: list = [None] * count
    failed_indices: list = []
    failed_errors: list[Exception] = []

    def _task(index: int):
        try:
            return index, _single_generate_dispatch(source_mode, source_kwargs, instruction, index)
        except Exception as exc:
            logger.warning('Batch generation failed batch_id=%s index=%s err=%s', batch_id, index, str(exc)[:200])
            return index, exc

    with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as pool:
        futures = [pool.submit(_task, i) for i in range(count)]
        for fut in as_completed(futures):
            idx, outcome = fut.result()
            if isinstance(outcome, Exception):
                failed_indices.append(idx)
                failed_errors.append(outcome)
            else:
                results[idx] = outcome

    successful = [r for r in results if r is not None]
    failed_indices.sort()

    logger.info(
        'Batch generation done batch_id=%s requested=%d succeeded=%d failed=%d',
        batch_id, count, len(successful), len(failed_indices),
    )

    batch = {
        'batch_id': batch_id,
        'count_requested': count,
        'count_succeeded': len(successful),
        'count_failed': len(failed_indices),
        'results': successful,
        'failed_indices': failed_indices,
    }
    if not successful and any(is_ai_quota_error(exc) for exc in failed_errors):
        batch['error'] = AI_QUOTA_MESSAGE
    return batch
