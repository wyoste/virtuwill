-- VirtuWill data model · career: the CV, the work ethos and the projects behind them
-- One public Career page reads these tables: who the owner is and what they
-- stand for, where they worked, what they know, where they studied, and the
-- projects explorer (content.portfolio_projects, each linked to the role it
-- came from). Everything here is public; nothing private belongs in it.

CREATE SCHEMA IF NOT EXISTS career;

-- The header and the ethos statement: one row.
CREATE TABLE IF NOT EXISTS career.profile (
    profile_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (profile_id = 1),
    full_name TEXT NOT NULL,
    headline TEXT NOT NULL DEFAULT '',          -- 'Enterprise Data Product Owner'
    organization TEXT NOT NULL DEFAULT '',      -- where, now
    location TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',             -- shown only when set
    linkedin_url TEXT NOT NULL DEFAULT '',
    ethos_eyebrow TEXT NOT NULL DEFAULT '',
    ethos_headline TEXT NOT NULL DEFAULT '',
    ethos_summary TEXT NOT NULL DEFAULT '',
    cv_asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,   -- an uploaded CV; else the bundled PDF
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Jobs, newest first by start date.
CREATE TABLE IF NOT EXISTS career.roles (
    role_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,                  -- stable name for links and the seed: 'greystar'
    title TEXT NOT NULL,
    organization TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    started_on DATE NOT NULL REFERENCES core.calendar (day),     -- first of the month
    ended_on DATE REFERENCES core.calendar (day),                -- NULL while current
    summary TEXT NOT NULL DEFAULT '',
    bullets TEXT[] NOT NULL DEFAULT '{}',       -- accomplishments, in display order
    tags TEXT[] NOT NULL DEFAULT '{}',          -- technologies and tools
    visible BOOLEAN NOT NULL DEFAULT true,
    CHECK (ended_on IS NULL OR ended_on >= started_on)
);

CREATE TABLE IF NOT EXISTS career.education (
    education_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    degree TEXT NOT NULL,                       -- 'Master of Science'
    field_of_study TEXT NOT NULL DEFAULT '',
    school TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    finished_on DATE REFERENCES core.calendar (day),
    grade TEXT NOT NULL DEFAULT '',             -- 'GPA 3.67'
    highlight_label TEXT NOT NULL DEFAULT '',   -- 'Academic project'
    highlight TEXT NOT NULL DEFAULT '',
    visible BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS career.skill_groups (
    group_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label TEXT NOT NULL,                        -- 'Cloud & data platforms'
    skills TEXT[] NOT NULL DEFAULT '{}',
    featured TEXT[] NOT NULL DEFAULT '{}',      -- the ones to emphasise
    position INTEGER NOT NULL DEFAULT 0
);

-- Short statements the ethos and skills sections are built from.
CREATE TABLE IF NOT EXISTS career.highlights (
    highlight_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section TEXT NOT NULL CHECK (section IN ('pillar', 'impact', 'strength', 'certification')),
    position INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,                        -- pillar name, impact figure ('$6M'), strength, certification
    body TEXT NOT NULL DEFAULT '',              -- what it means
    icon TEXT NOT NULL DEFAULT ''               -- one emoji or symbol
);
CREATE INDEX IF NOT EXISTS highlights_by_section ON career.highlights (section, position);

-- Each project can name the role it came from, so Experience and Projects link to each other.
ALTER TABLE content.portfolio_projects ADD COLUMN IF NOT EXISTS role_id BIGINT REFERENCES career.roles ON DELETE SET NULL;
