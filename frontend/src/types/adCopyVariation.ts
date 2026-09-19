export type CopyViolation = {
  field: "hook" | "headline" | "description";
  rule: "max_chars" | "max_words";
  limit: number;
  actual: number;
};
export type AdCopyVariationSourceMode = 'existing' | 'custom' | 'external_url';
export type AdCopyVariationStatus = 'draft' | 'reviewed';

export interface AdCopyVariationCopy {
  hook: string;
  headline: string;
  description: string;
  cta: string;
}

export interface AdCopyVariation extends AdCopyVariationCopy {
  id: number;
  slug?: string;
  project: number | null;
  creative: number | null;
  creative_slug?: string | null;
  source_mode: AdCopyVariationSourceMode;
  source_ref: string;
  validation_warnings: CopyViolation[];
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
  source_mode: AdCopyVariationSourceMode;
  count?: number;
  creative_id?: number;
  base_copy?: AdCopyVariationCopy;
  url?: string;
  instruction?: string;
}

export interface BatchGenerateResponse {
  batch_id: string;
  count_requested: number;
  count_succeeded: number;
  count_failed: number;
  results: AdCopyVariation[];
  failed_indices: number[];
  error?: string;
}

export interface SaveVariationRequest extends AdCopyVariationCopy {
  project?: number | string | null;
  source_mode: AdCopyVariationSourceMode;
  creative?: number | null;
  source_ref?: string;
  instruction?: string;
  batch_id?: string | null;
  batch_position?: number | null;
  status?: AdCopyVariationStatus;
}
