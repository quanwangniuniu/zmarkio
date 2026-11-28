import { ApiError } from '@/lib/bulk';
import { LEGACY_COPY_KEYS, LEGACY_COPY_PLATFORM_ID } from '@/lib/variationSchema';
import type { PlatformSpec } from '@/src/platforms';

/** A stored draft has a string for every field, including omitted optional copy. */
export function completeDraftFields(spec: PlatformSpec, copy: Record<string, string>): Record<string, string> {
  return Object.fromEntries(spec.fields.map(({ key }) => [
    key, Object.prototype.hasOwnProperty.call(copy, key) ? copy[key] : '',
  ]));
}

/** Read only the submitted copy, so PATCH can leave other fields untouched. */
export function readDraftFields(
  body: Record<string, unknown>,
  spec: PlatformSpec | undefined,
  operation: 'create' | 'patch'
): Record<string, string> {
  const legacyKeys = LEGACY_COPY_KEYS.filter(key => key in body);
  const hasFields = 'fields' in body;
  if (hasFields && legacyKeys.length) {
    throw new ApiError(400, 'fields cannot be combined with top-level copy fields');
  }
  if (spec && spec.id !== LEGACY_COPY_PLATFORM_ID && legacyKeys.length) {
    throw new ApiError(400, `Copy for platform "${spec.id}" must be submitted in fields`);
  }

  let entries: [string, unknown][];
  if (hasFields && spec) {
    const fields = body.fields;
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)) {
      throw new ApiError(400, 'fields must be an object');
    }
    entries = Object.entries(fields);
    for (const [key] of entries) {
      if (!spec.fields.some(field => field.key === key)) {
        throw new ApiError(400, `Unknown field "${key}" for platform "${spec.id}"`);
      }
    }
  } else {
    entries = legacyKeys.map(key => [key, body[key]]);
  }

  return Object.fromEntries(entries.map(([key, value]) => {
    // The legacy create payload treats a null top-level string as an empty string.
    if (!hasFields && operation === 'create' && value === null) return [key, ''];
    if (typeof value !== 'string') throw new ApiError(400, `${key} must be a string`);
    return [key, value];
  }));
}
