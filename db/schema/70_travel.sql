-- VirtuWill data model · travel
-- Map pins (visited, recommended, wishlist) and the countries and US states visited.

CREATE SCHEMA IF NOT EXISTS travel;

CREATE TABLE IF NOT EXISTS travel.places (
    place_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',  -- full geocoded address
    latitude NUMERIC(9,6) NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude NUMERIC(9,6) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    pin_type TEXT NOT NULL CHECK (pin_type IN ('visited', 'recommend', 'wishlist')),
    note TEXT NOT NULL DEFAULT '',
    visited_on DATE REFERENCES core.calendar (day),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS travel.place_photos (
    place_id TEXT NOT NULL REFERENCES travel.places ON DELETE CASCADE,
    position INTEGER NOT NULL,
    asset_id BIGINT REFERENCES core.media_assets ON DELETE CASCADE,
    url TEXT,                               -- an external image link
    caption TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (place_id, position),
    CHECK (asset_id IS NOT NULL OR url IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS travel.visited_regions (
    region_type TEXT NOT NULL CHECK (region_type IN ('country', 'us_state')),
    code TEXT NOT NULL,                     -- ISO 3166-1 alpha-2 country, or USPS state code
    first_visited_on DATE REFERENCES core.calendar (day),
    PRIMARY KEY (region_type, code),
    CHECK (code = upper(code) AND length(code) = 2)
);
