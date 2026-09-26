/**
 * MED-454: shared org-customization config types.
 *
 * Shapes match the ticket contract exactly and are what
 * GET /api/org-customization/effective/ returns (B4). Keys come from the
 * frozen unions in @/lib/orgCustomization/keys — no bare strings.
 */
import type { ModuleKey, SurfaceKey } from '@/lib/orgCustomization/keys';

export interface ModuleConfig {
  enabled: boolean;
  label: string | null; // null = use the registry default
  order: number;
}

export interface SurfaceConfig {
  visible: boolean;
  label: string | null;
  order: number;
}

export interface EffectiveConfig {
  version: number;
  modules: Record<ModuleKey, ModuleConfig>;
  surfaces: Record<SurfaceKey, SurfaceConfig>;
}

/** Registry entry shape returned by GET /api/org-customization/registry/. */
export interface RegistryEntry {
  key: string;
  label: string;
  default_visible: boolean;
  default_order: number;
  route_prefixes: string[];
}

export interface RegistryResponse {
  modules: RegistryEntry[];
  surfaces: RegistryEntry[];
}

export function isModuleConfig(value: unknown): value is ModuleConfig {
  if (typeof value !== 'object' || value === null) return false;
  const config = value as Record<string, unknown>;
  return (
    typeof config.enabled === 'boolean' &&
    (typeof config.label === 'string' || config.label === null) &&
    typeof config.order === 'number'
  );
}

export function isSurfaceConfig(value: unknown): value is SurfaceConfig {
  if (typeof value !== 'object' || value === null) return false;
  const config = value as Record<string, unknown>;
  return (
    typeof config.visible === 'boolean' &&
    (typeof config.label === 'string' || config.label === null) &&
    typeof config.order === 'number'
  );
}

export function isEffectiveConfig(value: unknown): value is EffectiveConfig {
  if (typeof value !== 'object' || value === null) return false;
  const config = value as Record<string, unknown>;
  if (typeof config.version !== 'number') return false;
  if (typeof config.modules !== 'object' || config.modules === null) return false;
  if (typeof config.surfaces !== 'object' || config.surfaces === null) return false;
  return (
    Object.values(config.modules).every(isModuleConfig) &&
    Object.values(config.surfaces).every(isSurfaceConfig)
  );
}
