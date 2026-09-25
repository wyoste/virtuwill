-- VirtuWill data model · school logos on the Career page's Education cards
-- A logo is a path under static/: a bundled seal in schools/, or one uploaded
-- in the workspace. The two schools already on the page get their bundled seals.

ALTER TABLE career.education ADD COLUMN IF NOT EXISTS logo_path TEXT NOT NULL DEFAULT '';

UPDATE career.education SET logo_path = 'schools/ut-dallas.png'
 WHERE logo_path = '' AND (school ILIKE '%Texas at Dallas%' OR school ILIKE 'UT Dallas%');
UPDATE career.education SET logo_path = 'schools/university-of-mississippi.png'
 WHERE logo_path = '' AND (school ILIKE '%University of Mississippi%' OR school ILIKE 'Ole Miss%');
