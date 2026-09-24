-- VirtuWill data model · meals made of several foods
-- An egg burrito is one meal of eggs, tortillas and olive oil, each with its own
-- portion and nutrition. The meal's nutrition columns hold the sum of its items,
-- so every view that reads journal.meals keeps working unchanged.

CREATE TABLE IF NOT EXISTS journal.meal_items (
    meal_id BIGINT NOT NULL REFERENCES journal.meals ON DELETE CASCADE,
    position INTEGER NOT NULL,
    food_id TEXT REFERENCES health.foods ON DELETE SET NULL,   -- NULL once the food is deleted; numbers stay
    description TEXT NOT NULL,              -- the food's name when logged
    quantity NUMERIC NOT NULL CHECK (quantity > 0),   -- servings of the food's unit
    unit TEXT NOT NULL DEFAULT '',
    calories NUMERIC,
    protein_g NUMERIC,
    carbs_g NUMERIC,
    fat_g NUMERIC,
    fiber_g NUMERIC,
    PRIMARY KEY (meal_id, position)
);
CREATE INDEX IF NOT EXISTS meal_items_by_food ON journal.meal_items (food_id);
