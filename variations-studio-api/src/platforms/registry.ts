import type { PlatformField, PlatformSpec } from './types';

/** Reject authored-spec mistakes that the PlatformSpec type cannot express. */
export function assertPlatformSpec(spec: PlatformSpec): void {
  const fail: (message: string) => never = (message) => {
    throw new Error(`Invalid platform spec "${spec.id}": ${message}`);
  };
  const fields = new Map<string, PlatformField>(spec.fields.map((field) => [field.key, field]));
  if (fields.size !== spec.fields.length) fail('duplicate field key');

  for (const field of spec.fields) {
    if (field.type === 'string[]' && (
      !Number.isSafeInteger(field.minItems) || !Number.isSafeInteger(field.maxItems)
      || field.minItems < 0 || field.minItems > field.maxItems
    )) {
      fail(`${field.key} has invalid cardinality`);
    }
  }
  if (spec.cta.kind !== 'none') {
    const field = fields.get(spec.cta.field);
    if (field?.type !== 'string' || !field.required) fail('CTA must reference a required string field');
    if (spec.cta.kind === 'enum' && !spec.cta.values.includes(spec.cta.fallback)) {
      fail('CTA fallback is not in the enum');
    }
  }

  const slug = fields.get(spec.slugSource.field);
  const { index } = spec.slugSource;
  if (!slug?.required) fail('slugSource must reference a required field');
  const indexFits = slug.type === 'string[]'
    ? index !== undefined && Number.isSafeInteger(index) && index >= 0 && index < slug.minItems
    : index === undefined;
  if (!indexFits) fail('slugSource index does not fit the field');
}

export function createPlatformRegistry(specs: readonly PlatformSpec[]) {
  specs.forEach(assertPlatformSpec);
  const registry = new Map<string, PlatformSpec>(specs.map((spec) => [spec.id, spec]));
  if (registry.size !== specs.length) throw new Error('Duplicate platform id');

  return {
    getPlatformSpec(id: string): PlatformSpec {
      const spec = registry.get(id);
      if (!spec) throw new Error(`Unknown platform "${id}"`);
      return spec;
    },
    listPlatformIds(): string[] {
      return [...registry.keys()];
    },
  };
}
