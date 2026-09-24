-- VirtuWill data model · content
-- The public site: blog posts, portfolio projects, editable page text, and
-- messages visitors leave through the contact form.

CREATE SCHEMA IF NOT EXISTS content;

CREATE TABLE IF NOT EXISTS content.blog_posts (
    post_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',          -- rich text (HTML)
    excerpt TEXT NOT NULL DEFAULT '',
    thumbnail_asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,
    author TEXT NOT NULL DEFAULT 'Will Yoste',
    published BOOLEAN NOT NULL DEFAULT false,
    post_date DATE NOT NULL REFERENCES core.calendar (day),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS posts_by_date ON content.blog_posts (post_date);

-- Built-in projects from the page and uploaded HTML projects, with the
-- owner's show/hide/remove choices.
CREATE TABLE IF NOT EXISTS content.portfolio_projects (
    project_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT 'Project',
    tag_class TEXT NOT NULL DEFAULT 'platform',   -- styling group: data, platform …
    description TEXT NOT NULL DEFAULT '',   -- card summary
    subtitle TEXT NOT NULL DEFAULT '',
    overview TEXT NOT NULL DEFAULT '',
    chips TEXT[] NOT NULL DEFAULT '{}',     -- technologies, in display order
    html_asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,
    is_builtin BOOLEAN NOT NULL DEFAULT false,
    visible BOOLEAN NOT NULL DEFAULT true,
    removed BOOLEAN NOT NULL DEFAULT false,
    position INTEGER,
    uploaded_on DATE REFERENCES core.calendar (day),
    CHECK (is_builtin OR html_asset_id IS NOT NULL OR removed)
);

CREATE TABLE IF NOT EXISTS content.project_metrics (
    project_id TEXT NOT NULL REFERENCES content.portfolio_projects ON DELETE CASCADE,
    position INTEGER NOT NULL,
    value TEXT NOT NULL,                    -- '$6M', '2,000+'
    label TEXT NOT NULL,
    PRIMARY KEY (project_id, position)
);

CREATE TABLE IF NOT EXISTS content.project_timeline (
    project_id TEXT NOT NULL REFERENCES content.portfolio_projects ON DELETE CASCADE,
    position INTEGER NOT NULL,
    period TEXT NOT NULL,                   -- '2025'
    phase TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, position)
);

-- Short editable text on public pages, e.g. the garden gallery quote and blurb.
CREATE TABLE IF NOT EXISTS content.site_text (
    text_key TEXT PRIMARY KEY,              -- 'garden.gallery_note', 'garden.hero'
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content.messages (
    message_id TEXT PRIMARY KEY,
    sender_name TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    received_on DATE NOT NULL REFERENCES core.calendar (day),
    received_at TIMESTAMPTZ,
    is_read BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS messages_unread ON content.messages (received_on) WHERE NOT is_read;

DROP VIEW IF EXISTS content.public_posts CASCADE;
CREATE VIEW content.public_posts AS
SELECT p.post_id, p.title, p.excerpt, p.body, p.author, p.post_date, m.path AS thumbnail_path
FROM content.blog_posts p
LEFT JOIN core.media_assets m ON m.asset_id = p.thumbnail_asset_id
WHERE p.published;
