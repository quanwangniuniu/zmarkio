// Shared runtime schema contract. The variation table name matches the SQL migrations.
export const VARIATION_TABLE = 'ad_copy_variation_adcopyvariation';
export const STUDIO_MIGRATIONS_TABLE = 'studio_migrations';

// Studio requires this recorded version, even when newer versions are present.
export const REQUIRED_STUDIO_MIGRATION = '003';
