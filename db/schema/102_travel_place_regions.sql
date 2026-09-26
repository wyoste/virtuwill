-- VirtuWill data model · which country and state (or region) each stop is in
-- So the countries and US states visited can count the stops themselves: a
-- stop in New York City marks New York as visited. NULL means not looked up
-- yet (the app fills these in from each stop's position on start); '' means
-- the stop is somewhere with no country or region to name.

ALTER TABLE travel.places ADD COLUMN IF NOT EXISTS country_code TEXT
    CHECK (country_code IS NULL OR country_code = '' OR country_code ~ '^[A-Z]{2}$');
ALTER TABLE travel.places ADD COLUMN IF NOT EXISTS region_code TEXT
    CHECK (region_code IS NULL OR length(region_code) <= 10);   -- a US state's USPS code; elsewhere the GeoNames region code
