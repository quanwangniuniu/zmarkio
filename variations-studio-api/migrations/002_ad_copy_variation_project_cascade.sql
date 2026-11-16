-- Raw SQL project deletion must also remove its tenant-local variations.
-- Keep the constraint name and deferred checks; shared creative/user FKs stay unchanged.
ALTER TABLE ad_copy_variation_adcopyvariation
    DROP CONSTRAINT ad_copy_variation_ad_project_id_abef521f_fk_core_proj,
    ADD CONSTRAINT ad_copy_variation_ad_project_id_abef521f_fk_core_proj
        FOREIGN KEY (project_id) REFERENCES core_project(id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
