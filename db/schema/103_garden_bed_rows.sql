-- VirtuWill data model · rows in a bed, and each plant's seat in its row
-- A plant's spot is its row and seat: "B4" is the fourth plant in row B, like
-- a square on a chess board. Rows are their own things (a bed has rows, a row
-- has plants), and the spot is stored, so a plant keeps it for life: moving it
-- or planting a neighbour never renumbers anything. The app gives new plants a
-- spot when the planner saves: the nearest row by height, the next free seat.

CREATE TABLE IF NOT EXISTS garden.bed_rows (
    row_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bed_id TEXT NOT NULL REFERENCES garden.beds ON DELETE CASCADE,
    label TEXT NOT NULL CHECK (label ~ '^[A-Z]+$'),     -- A, B, … Z, AA
    grid_j INTEGER NOT NULL,                            -- where the row runs, on the planner's grid (top to bottom)
    UNIQUE (bed_id, label)
);

ALTER TABLE garden.plantings ADD COLUMN IF NOT EXISTS row_id BIGINT REFERENCES garden.bed_rows ON DELETE SET NULL;
ALTER TABLE garden.plantings ADD COLUMN IF NOT EXISTS seat INTEGER CHECK (seat IS NULL OR seat > 0);
-- A seat belongs to one plant, even after it's removed, so a spot is never reused.
CREATE UNIQUE INDEX IF NOT EXISTS plantings_one_per_seat ON garden.plantings (row_id, seat) WHERE row_id IS NOT NULL;
