-- VirtuWill data model · go-to meal portions
-- A cooked batch (a sushi bowl, a Mexican scramble, a family meal) makes several
-- portions; its nutrition is shown per portion, and logging it starts at one portion.
-- Ingredient quantities stay per batch; optional extras (meatballs, cheese) are per portion.

ALTER TABLE health.recipes ADD COLUMN IF NOT EXISTS portions NUMERIC NOT NULL DEFAULT 1
    CHECK (portions > 0 AND portions <= 100);

-- The starter meals already added (db/seed/foods*.json), as usually made.
UPDATE health.recipes SET portions = 4 WHERE source = 'seed' AND name = 'Sushi Bowls';
UPDATE health.recipes SET portions = 6 WHERE source = 'seed' AND name LIKE 'Mexican Scramble%';
UPDATE health.recipes SET portions = 4 WHERE source = 'seed' AND name = 'Spaghetti Squash';
