import { metaSpec } from './meta';
import { createPlatformRegistry } from './registry';
import type { PlatformSpec } from './types';

export type {
  CtaPolicy, PlatformCopy, PlatformField, PlatformPresentation, PlatformSpec, TextLimits,
} from './types';
export { assertPlatformSpec, createPlatformRegistry } from './registry';
export { toPlatformPresentation } from './presentation';

export const platformRegistry: Map<string, PlatformSpec> = createPlatformRegistry([metaSpec]);

export function getPlatformSpec(id: string): PlatformSpec {
  const spec = platformRegistry.get(id);
  if (!spec) throw new Error(`Unknown platform "${id}"`);
  return spec;
}
