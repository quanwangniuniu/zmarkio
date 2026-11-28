// Shared runtime schema contract. The variation table name matches the SQL migrations.
export const VARIATION_TABLE = 'ad_copy_variation_adcopyvariation';
export const STUDIO_MIGRATIONS_TABLE = 'studio_migrations';

// Migration 003 keeps these columns authoritative for existing Meta drafts.
export const LEGACY_COPY_PLATFORM_ID = 'meta';
export const LEGACY_COPY_KEYS = ['hook', 'headline', 'description', 'cta'] as const;

// Studio requires this recorded version, even when newer versions are present.
export const REQUIRED_STUDIO_MIGRATION = '003';
