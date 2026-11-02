import type { ModuleKey, SurfaceKey, SurfaceTypeKey } from '@/lib/orgCustomization/keys';

export interface ModuleConfig {
  enabled: boolean;
  label: string | null;
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

export interface ModuleSpec {
  key: ModuleKey;
  label: string;
  default_visible: boolean;
  default_order: number;
  route_prefixes: string[];
}

export interface SurfaceTypeSpec {
  key: SurfaceTypeKey;
  label: string;
}

export interface SurfaceSpec {
  key: SurfaceKey;
  label: string;
  default_visible: boolean;
  default_order: number;
  route_prefixes: string[];
  parent: SurfaceKey | null;
}

export interface Registry {
  modules: ModuleSpec[];
  surface_types: SurfaceTypeSpec[];
  surfaces: SurfaceSpec[];
}
