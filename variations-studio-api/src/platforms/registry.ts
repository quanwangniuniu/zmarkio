import type { PlatformSpec, TextLimits } from './types';

type Fail = (message: string) => never;
type FieldIndex = ReadonlyMap<string, PlatformSpec['fields'][number]>;

/** Check relationships and numeric bounds in TypeScript-authored platform specs. */
export function assertPlatformSpec(spec: PlatformSpec): void {
  const fail: Fail = (message) => {
    throw new Error(`Invalid platform spec "${spec.id}": ${message}`);
  };

  const fields = assertFieldsAndBuildIndex(spec.fields, fail);
  assertCta(spec.cta, fields, fail);
  assertLanguage(spec.language, fields, fail);
  assertSlugSource(spec.slugSource, fields, fail);
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

function assertTextLimits(limits: TextLimits, field: string, fail: Fail): void {
  for (const name of ['maxChars', 'maxWords'] as const) {
    const value = limits[name];
    if (value !== undefined && (!Number.isSafeInteger(value) || value <= 0)) {
      fail(`${field}.${name} must be a positive integer`);
    }
  }
  if (limits.characterCounting !== undefined && limits.maxChars === undefined) {
    fail(`${field} has invalid characterCounting`);
  }
}

function assertFieldsAndBuildIndex(fieldSpecs: PlatformSpec['fields'], fail: Fail): FieldIndex {
  if (!fieldSpecs.length) fail('at least one field is required');
  const fields = new Map<string, PlatformSpec['fields'][number]>();
  for (const field of fieldSpecs) {
    if (fields.has(field.key)) fail(`duplicate field "${field.key}"`);
    fields.set(field.key, field);
    if (field.type === 'string') {
      assertTextLimits(field.limits, field.key, fail);
    } else {
      if (!Number.isSafeInteger(field.minItems) || field.minItems < 0
        || !Number.isSafeInteger(field.maxItems) || field.maxItems < 1
        || field.minItems > field.maxItems) {
        fail(`${field.key} has invalid cardinality`);
      }
      assertTextLimits(field.itemLimits, field.key, fail);
    }
  }
  return fields;
}

function assertCta(cta: PlatformSpec['cta'], fields: FieldIndex, fail: Fail): void {
  if (cta.kind === 'none') return;
  const ctaField = fields.get(cta.field);
  if (!ctaField || ctaField.type !== 'string') fail('CTA must reference a scalar field');
  if (cta.kind === 'enum') {
    if (cta.values.some((value) => !value.trim())
      || new Set(cta.values).size !== cta.values.length) {
      fail('CTA enum must contain unique non-empty values');
    }
    if (!cta.values.includes(cta.fallback)) fail('CTA fallback is not in the enum');
  }
}

function assertLanguage(language: PlatformSpec['language'], fields: FieldIndex, fail: Fail): void {
  for (const field of language.excludedFields) {
    if (!fields.has(field)) fail(`language rule references undeclared field "${field}"`);
  }
}

function assertSlugSource(slugSource: PlatformSpec['slugSource'], fields: FieldIndex, fail: Fail): void {
  const slug = fields.get(slugSource.field);
  if (!slug) fail('slugSource references an undeclared field');
  if (slug.type === 'string[]') {
    const index = slugSource.index;
    if (index === undefined || !Number.isSafeInteger(index) || index < 0
      || index >= slug.minItems) {
      fail('slugSource index must exist within the minimum cardinality');
    }
  } else if (slugSource.index !== undefined) {
    fail('scalar slugSource must not have an index');
  }
  if (!slug.required) fail('slugSource must reference a required field');
}
