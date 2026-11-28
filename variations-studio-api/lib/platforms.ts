import { ApiError } from '@/src/platform/http';
import {
  DEFAULT_PLATFORM_ID, getPlatformSpec, listPlatformIds,
  type PlatformField, type PlatformSpec, type TextLimits,
} from '@/src/platforms';

export function resolveRequestPlatform(value: unknown): PlatformSpec {
  const id = value == null || value === '' ? DEFAULT_PLATFORM_ID : value;
  if (typeof id !== 'string' || !listPlatformIds().includes(id)) {
    throw new ApiError(400, 'platform must be a registered platform');
  }
  return getPlatformSpec(id);
}

function serializeLimits(limits: TextLimits) {
  return {
    max_chars: limits.maxChars,
    max_words: limits.maxWords,
    character_counting: limits.characterCounting,
  };
}

function serializeField(field: PlatformField) {
  const { key, label, required, type } = field;
  if (type === 'string') {
    return { key, label, required, type, limits: serializeLimits(field.limits) };
  }
  return {
    key, label, required, type,
    min_items: field.minItems,
    max_items: field.maxItems,
    item_limits: serializeLimits(field.itemLimits),
  };
}

export function serializePlatform({ id, displayName, fields, cta }: PlatformSpec) {
  return {
    id,
    display_name: displayName,
    fields: fields.map(serializeField),
    cta: cta.kind === 'enum' ? { kind: cta.kind, field: cta.field, values: cta.values } : cta,
  };
}
