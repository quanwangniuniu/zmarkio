import type { PlatformSpec, TextLimits } from './types';

/** Check authored configuration, not user copy. Content validation belongs to C5. */
export function assertPlatformSpec(spec: PlatformSpec): void {
  const fail = (message: string): never => {
    throw new Error(`Invalid platform spec "${spec.id}": ${message}`);
  };
  const checkLimits = (limits: TextLimits, field: string) => {
    for (const [name, value] of Object.entries(limits)) {
      if (name === 'characterCounting') {
        if (typeof value !== 'string' || !['unicode-code-points', 'double-width'].includes(value)
          || limits.maxChars === undefined) {
          fail(`${field} has invalid characterCounting`);
        }
        continue;
      }
      if (!['maxChars', 'maxWords'].includes(name)
        || typeof value !== 'number' || !Number.isSafeInteger(value) || value <= 0) {
        fail(`${field}.${name} must be a positive integer`);
      }
    }
  };

  if (!/^[a-z][a-z0-9_-]*$/.test(spec.id)) fail('id must be a stable lowercase key');
  if (!spec.displayName.trim()) fail('displayName is required');
  if (!spec.promptFragment.trim() || !spec.promptVersion.trim()) {
    fail('promptFragment and promptVersion are required');
  }
  if (!spec.fields.length) fail('at least one field is required');
  const fields = new Map<string, PlatformSpec['fields'][number]>();
  for (const field of spec.fields) {
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(field.key)) fail('invalid field key');
    if (fields.has(field.key)) fail(`duplicate field "${field.key}"`);
    if (!field.label.trim()) fail(`${field.key} needs a label`);
    if (typeof field.required !== 'boolean') fail(`${field.key} needs required`);
    fields.set(field.key, field);
    if (field.type === 'string') {
      checkLimits(field.limits, field.key);
    } else if (field.type === 'string[]') {
      if (!Number.isSafeInteger(field.minItems) || field.minItems < 0
        || !Number.isSafeInteger(field.maxItems) || field.maxItems < 1
        || field.minItems > field.maxItems) {
        fail(`${field.key} has invalid cardinality`);
      }
      checkLimits(field.itemLimits, field.key);
    } else {
      fail('unsupported field type');
    }
  }

  if (spec.cta.kind !== 'none') {
    const ctaField = fields.get(spec.cta.field);
    if (!ctaField || ctaField.type !== 'string') fail('CTA must reference a scalar field');
    if (spec.cta.kind === 'enum') {
      if (!spec.cta.values.length || spec.cta.values.some((value) => !value.trim())
        || new Set(spec.cta.values).size !== spec.cta.values.length) {
        fail('CTA enum must contain unique non-empty values');
      }
      if (!spec.cta.values.includes(spec.cta.fallback)) fail('CTA fallback is not in the enum');
      if (!['exact', 'trim-uppercase-underscores'].includes(spec.cta.normalization)) {
        fail('unsupported CTA normalization');
      }
    } else if (spec.cta.kind !== 'freeText') {
      fail('unsupported CTA policy');
    }
  }

  if (spec.language.mode !== 'source') fail('unsupported language mode');
  for (const field of spec.language.excludedFields) {
    if (!fields.has(field)) fail(`language rule references undeclared field "${field}"`);
  }
  for (const ratio of spec.aspectRatios) {
    if (!/^\d+(?:\.\d+)?:\d+(?:\.\d+)?$/.test(ratio)
      || ratio.split(':').some((part) => !Number.isFinite(Number(part)) || Number(part) <= 0)) {
      fail(`invalid aspect ratio "${ratio}"`);
    }
  }

  const slug = fields.get(spec.slugSource.field);
  if (!slug) fail('slugSource references an undeclared field');
  if (slug?.type === 'string[]') {
    const index = spec.slugSource.index;
    if (index === undefined || !Number.isSafeInteger(index) || index < 0
      || index >= slug.minItems) {
      fail('slugSource index must exist within the minimum cardinality');
    }
  } else if (spec.slugSource.index !== undefined) {
    fail('scalar slugSource must not have an index');
  }
  if (!slug?.required) fail('slugSource must reference a required field');
}

export function createPlatformRegistry(specs: readonly PlatformSpec[]): Map<string, PlatformSpec> {
  const registry = new Map<string, PlatformSpec>();
  for (const spec of specs) {
    assertPlatformSpec(spec);
    if (registry.has(spec.id)) throw new Error(`Duplicate platform id "${spec.id}"`);
    registry.set(spec.id, spec);
  }
  return registry;
}
