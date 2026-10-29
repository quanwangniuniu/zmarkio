-- Keep legacy copy columns authoritative while adding platform-shaped storage.
-- Complete DDL before backfilling; the runner owns the transaction and ledger.
ALTER TABLE ad_copy_variation_adcopyvariation
    ADD COLUMN platform text NOT NULL DEFAULT 'meta',
    ADD COLUMN fields jsonb;

CREATE INDEX ad_copy_variation_platform_idx
    ON ad_copy_variation_adcopyvariation (platform);

UPDATE ad_copy_variation_adcopyvariation
SET fields = jsonb_build_object(
    'hook', hook, 'headline', headline, 'description', description, 'cta', cta)
WHERE fields IS NULL;
