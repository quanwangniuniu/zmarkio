import { metaSpec } from './meta';
import { createPlatformRegistry } from './registry';

export type { CtaPolicy, PlatformField, PlatformSpec, TextLimits } from './types';
export { assertPlatformSpec, createPlatformRegistry } from './registry';

export const { getPlatformSpec, listPlatformIds } = createPlatformRegistry([metaSpec]);
