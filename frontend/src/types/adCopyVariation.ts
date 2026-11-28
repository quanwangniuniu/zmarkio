export type AdCopyVariationSourceMode = 'existing' | 'custom' | 'external_url';
export type AdCopyVariationStatus = 'draft' | 'reviewed';

export type AdCopyVariationCopy = Record<string, string>;

interface StudioTextLimits {
  max_chars?: number;
  max_words?: number;
  character_counting?: 'unicode-code-points' | 'double-width';
}

export type StudioPlatformField = {
  key: string;
  label: string;
  required: boolean;
} & (
  | { type: 'string'; limits: StudioTextLimits }
  | { type: 'string[]'; min_items: number; max_items: number; item_limits: StudioTextLimits }
);

export interface StudioPlatformSpec {
  id: string;
  display_name: string;
  fields: StudioPlatformField[];
  cta:
    | { kind: 'none' }
    | { kind: 'freeText'; field: string }
    | { kind: 'enum'; field: string; values: string[] };
}

interface LegacyMetaCopy {
  hook: string;
  headline: string;
  description: string;
  cta: string;
}

export interface AdCopyVariation extends LegacyMetaCopy {
  id: number;
  slug: string;
  platform: string;
  fields: Record<string, string | string[]>;
  project: number | null;
  creative: number | null;
  source_mode: AdCopyVariationSourceMode;
  source_ref: string;
  instruction: string;
  model_name: string;
  prompt_version: string;
  batch_id?: string | null;
  batch_position?: number | null;
  status: AdCopyVariationStatus;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}

export interface GenerateVariationRequest {
  project_id: number | string;
  platform?: string | null;
  source_mode: AdCopyVariationSourceMode;
  count?: number;
  creative_id?: number;
  base_copy?: AdCopyVariationCopy;
  url?: string;
  instruction?: string;
}

export interface BatchFieldFailure {
  index: number;
  violations: {
    field: string;
    rule: string;
    actual: number;
    limit: number;
  }[];
}

export interface BatchGenerateResponse {
  batch_id: string;
  count_requested: number;
  count_succeeded: number;
  count_failed: number;
  results: AdCopyVariation[];
  failed_indices: number[];
  field_failures?: BatchFieldFailure[];
  error?: string;
}

export interface SaveVariationRequest extends LegacyMetaCopy {
  project?: number | string | null;
  source_mode: AdCopyVariationSourceMode;
  creative?: number | null;
  source_ref?: string;
  instruction?: string;
  batch_id?: string | null;
  batch_position?: number | null;
  status?: AdCopyVariationStatus;
}
