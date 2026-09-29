-- VirtuWill data model · food groups
-- Saved foods are grouped (Grains, Fruit, Vegetables, Protein, Other) so the meal
-- logger can list them by group. db/seed/foods.json adds a starter list once.
-- A recipe is a usual meal: picking it preselects its foods. Optional ingredients
-- (meatballs, cheese) are offered, not preselected.

ALTER TABLE health.foods ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'Other'
    CHECK (category IN ('Grains', 'Fruit', 'Vegetables', 'Protein', 'Dairy', 'Other'));
CREATE INDEX IF NOT EXISTS foods_by_category ON health.foods (category, name);

ALTER TABLE health.recipe_ingredients ADD COLUMN IF NOT EXISTS optional BOOLEAN NOT NULL DEFAULT false;
