ALTER TABLE migrate_probe ADD COLUMN note text;
UPDATE migrate_probe SET note = 'updated' WHERE id = 1;
