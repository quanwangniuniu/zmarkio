/** Runtime data shared by validation, prompt assembly and form rendering. */
export type TextLimits = {
  readonly maxChars?: number;
  readonly maxWords?: number;
  /** Defaults to Unicode code points; Google counts double-width characters twice. */
  readonly characterCounting?: 'unicode-code-points' | 'double-width';
};

type FieldBase = {
  readonly key: string;
  readonly label: string;
  /** Presence in a generated copy object; not a manual-input coercion policy. */
  readonly required: boolean;
};

export type PlatformField = FieldBase & (
  | { readonly type: 'string'; readonly limits: TextLimits }
  | {
      readonly type: 'string[]';
      readonly minItems: number;
      readonly maxItems: number;
      readonly itemLimits: TextLimits;
    }
);

export type CtaPolicy =
  | { readonly kind: 'none' }
  | { readonly kind: 'freeText'; readonly field: string }
  | {
      readonly kind: 'enum';
      readonly field: string;
      readonly values: readonly string[];
      readonly normalization: 'exact' | 'trim-uppercase-underscores';
      readonly fallback: string;
    };

export type PlatformSpec = {
  readonly id: string;
  readonly displayName: string;
  /** Array order is the field order for prompts and forms. */
  readonly fields: readonly PlatformField[];
  readonly cta: CtaPolicy;
  /** Empty means not applicable (for example, a text-only search ad). */
  readonly aspectRatios: readonly string[];
  readonly language: {
    readonly mode: 'source';
    /** Fields such as enum tokens must not be translated. */
    readonly excludedFields: readonly string[];
  };
  readonly promptFragment: string;
  readonly promptVersion: string;
  /** Index selects one item of a repeated field. */
  readonly slugSource: { readonly field: string; readonly index?: number };
};
