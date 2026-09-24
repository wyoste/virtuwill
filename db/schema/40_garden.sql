-- VirtuWill data model · garden
-- The garden map: beds with their shapes, plantings on a 0.25 ft grid, species,
-- seasons, plant health over time, and photos.

CREATE SCHEMA IF NOT EXISTS garden;

CREATE TABLE IF NOT EXISTS garden.settings (
    settings_id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (settings_id = 1),
    background_asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,
    feet_per_pixel NUMERIC CHECK (feet_per_pixel IS NULL OR feet_per_pixel > 0),
    grid_ft NUMERIC NOT NULL DEFAULT 0.25
);

CREATE TABLE IF NOT EXISTS garden.species (
    species_id TEXT PRIMARY KEY,            -- e.g. 'hybrid-tea-rose'
    name TEXT NOT NULL,
    category TEXT NOT NULL,                 -- Rose, Perennial, Bulb …
    mature_diameter_ft NUMERIC NOT NULL CHECK (mature_diameter_ft > 0),
    emoji TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS garden.health_levels (
    level SMALLINT PRIMARY KEY CHECK (level BETWEEN 0 AND 4),
    label TEXT NOT NULL UNIQUE,
    icon TEXT NOT NULL
);
INSERT INTO garden.health_levels VALUES
    (0, 'Dead', '💀'), (1, 'Struggling', '🥀'), (2, 'Growing', '🌱'), (3, 'Blooming', '🌸'), (4, 'Full Bloom', '🌺')
ON CONFLICT (level) DO NOTHING;

-- Positions are in feet on the garden map; shape dimensions depend on the type.
CREATE TABLE IF NOT EXISTS garden.beds (
    bed_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#5DCAA5',
    shape_type TEXT NOT NULL CHECK (shape_type IN ('rectangle', 'circle', 'polygon')),
    width_ft NUMERIC,
    height_ft NUMERIC,
    radius_ft NUMERIC,
    vertices JSONB,                         -- [{x, y}, …] in bed-local feet, for polygons
    x_ft NUMERIC NOT NULL DEFAULT 0,
    y_ft NUMERIC NOT NULL DEFAULT 0,
    rotation_deg NUMERIC NOT NULL DEFAULT 0,
    position INTEGER,
    CHECK (shape_type <> 'rectangle' OR (width_ft > 0 AND height_ft > 0)),
    CHECK (shape_type <> 'circle' OR radius_ft > 0),
    CHECK (shape_type <> 'polygon' OR jsonb_typeof(vertices) = 'array')
);

-- A bed's layout for a season; new seasons start from the previous layout.
CREATE TABLE IF NOT EXISTS garden.seasons (
    season_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bed_id TEXT NOT NULL REFERENCES garden.beds ON DELETE CASCADE,
    label TEXT NOT NULL,                    -- e.g. 'Spring 2026'
    started_on DATE REFERENCES core.calendar (day),
    note TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (bed_id, label)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_season_per_bed ON garden.seasons (bed_id) WHERE is_active;

CREATE TABLE IF NOT EXISTS garden.plantings (
    planting_id TEXT PRIMARY KEY,           -- e.g. 'p1026'
    bed_id TEXT NOT NULL REFERENCES garden.beds ON DELETE CASCADE,
    season_id BIGINT REFERENCES garden.seasons ON DELETE CASCADE,
    species_id TEXT NOT NULL REFERENCES garden.species,
    display_name TEXT NOT NULL,
    grid_i INTEGER NOT NULL,                -- bed-local grid index; x = grid_i × grid_ft
    grid_j INTEGER NOT NULL,
    health SMALLINT NOT NULL DEFAULT 2 REFERENCES garden.health_levels,
    notes TEXT NOT NULL DEFAULT '',
    radius_ft NUMERIC CHECK (radius_ft IS NULL OR radius_ft > 0),   -- overrides the species' mature size
    age_years NUMERIC CHECK (age_years IS NULL OR age_years >= 0),
    planted_on DATE REFERENCES core.calendar (day),
    removed_on DATE REFERENCES core.calendar (day)
);
CREATE INDEX IF NOT EXISTS plantings_by_bed ON garden.plantings (bed_id, season_id);

-- Health over time; plantings.health is the latest observation.
CREATE TABLE IF NOT EXISTS garden.plant_observations (
    observation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    planting_id TEXT NOT NULL REFERENCES garden.plantings ON DELETE CASCADE,
    observed_on DATE NOT NULL REFERENCES core.calendar (day),
    health SMALLINT NOT NULL REFERENCES garden.health_levels,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS observations_by_date ON garden.plant_observations (observed_on);

CREATE TABLE IF NOT EXISTS garden.photos (
    photo_id TEXT PRIMARY KEY,
    asset_id BIGINT NOT NULL REFERENCES core.media_assets,
    taken_on DATE REFERENCES core.calendar (day),
    caption TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT ''
);
-- What a photo shows: beds, plantings or species.
CREATE TABLE IF NOT EXISTS garden.photo_subjects (
    photo_id TEXT NOT NULL REFERENCES garden.photos ON DELETE CASCADE,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('bed', 'planting', 'species', 'label')),
    subject_ref TEXT NOT NULL,
    PRIMARY KEY (photo_id, subject_type, subject_ref)
);

DROP VIEW IF EXISTS garden.bed_summary, garden.species_inventory CASCADE;

CREATE VIEW garden.bed_summary AS
SELECT b.bed_id, b.name, b.shape_type,
       CASE b.shape_type WHEN 'rectangle' THEN b.width_ft * b.height_ft
                         WHEN 'circle' THEN ROUND(pi()::numeric * b.radius_ft ^ 2, 1)
                         ELSE (SELECT ROUND(abs(SUM((v.value ->> 'x')::numeric * (n.value ->> 'y')::numeric
                                                   - (n.value ->> 'x')::numeric * (v.value ->> 'y')::numeric)) / 2, 1)
                               FROM jsonb_array_elements(b.vertices) WITH ORDINALITY v
                               JOIN jsonb_array_elements(b.vertices) WITH ORDINALITY n
                                 ON n.ordinality = v.ordinality % jsonb_array_length(b.vertices) + 1)
       END AS area_sqft,   -- polygons use the shoelace formula
       COUNT(p.planting_id) FILTER (WHERE p.removed_on IS NULL) AS plants,
       COUNT(DISTINCT p.species_id) FILTER (WHERE p.removed_on IS NULL) AS species,
       ROUND(AVG(p.health) FILTER (WHERE p.removed_on IS NULL), 2) AS avg_health,
       COUNT(*) FILTER (WHERE p.health <= 1 AND p.removed_on IS NULL) AS plants_needing_attention
FROM garden.beds b
LEFT JOIN garden.plantings p ON p.bed_id = b.bed_id
GROUP BY b.bed_id;

CREATE VIEW garden.species_inventory AS
SELECT s.species_id, s.name, s.category,
       COUNT(p.planting_id) AS plants,
       COUNT(DISTINCT p.bed_id) AS beds,
       ROUND(AVG(p.health), 2) AS avg_health
FROM garden.species s
LEFT JOIN garden.plantings p ON p.species_id = s.species_id AND p.removed_on IS NULL
GROUP BY s.species_id;
