-- Public reference excerpts for Loom. This is a portable research seed database.
-- owner_user_id identifies an eventual internal user; external_author is attribution only.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE CHECK(length(trim(url)) > 0),
    external_author TEXT NOT NULL CHECK(length(trim(external_author)) > 0),
    source_kind TEXT NOT NULL CHECK(source_kind IN ('public_note')),
    license_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(license_status IN ('unknown', 'open')),
    license_name TEXT,
    license_url TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    language TEXT NOT NULL CHECK(language IN ('en', 'zh')),
    original_excerpt TEXT NOT NULL
        CHECK(length(trim(original_excerpt)) > 0 AND length(original_excerpt) <= 500),
    original_sha256 TEXT NOT NULL UNIQUE
        CHECK(length(original_sha256) = 64 AND original_sha256 NOT GLOB '*[^0-9a-f]*'),
    excerpt_locator TEXT NOT NULL CHECK(length(trim(excerpt_locator)) > 0),
    retrieved_on TEXT NOT NULL,
    content_origin TEXT NOT NULL DEFAULT 'public_excerpt'
        CHECK(content_origin = 'public_excerpt'),
    split TEXT NOT NULL CHECK(split IN ('dev', 'test')),
    training_allowed INTEGER NOT NULL DEFAULT 0 CHECK(training_allowed IN (0, 1)),
    owner_user_id TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- A later web comparison is evidence about a source, not approval of its summary.
CREATE TABLE IF NOT EXISTS source_checks (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    note_id TEXT NOT NULL,
    checked_on TEXT NOT NULL,
    checker_type TEXT NOT NULL CHECK(checker_type IN ('ai_assisted', 'human')),
    fetch_status TEXT NOT NULL CHECK(fetch_status IN ('accessible', 'blocked', 'unavailable')),
    excerpt_status TEXT NOT NULL
        CHECK(excerpt_status IN ('exact', 'normalized_match', 'not_found', 'not_checked')),
    author_check TEXT NOT NULL CHECK(author_check IN ('confirmed', 'conflict', 'not_checked')),
    license_check TEXT NOT NULL
        CHECK(license_check IN ('confirmed', 'not_confirmed', 'conflict', 'not_checked')),
    license_evidence_url TEXT,
    notes TEXT NOT NULL CHECK(length(trim(notes)) > 0),
    method TEXT NOT NULL CHECK(method IN ('web_reader', 'human_browser')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY(note_id,source_id) REFERENCES notes(id,source_id),
    CHECK(fetch_status = 'accessible' OR
        (excerpt_status = 'not_checked' AND author_check = 'not_checked')),
    CHECK(license_check NOT IN ('confirmed', 'conflict') OR
        length(trim(coalesce(license_evidence_url, ''))) > 0)
);

CREATE TABLE IF NOT EXISTS annotations (
    note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    summary_zh TEXT NOT NULL CHECK(length(trim(summary_zh)) > 0),
    category TEXT NOT NULL CHECK(category IN ('question', 'idea', 'note', 'event')),
    summary_origin TEXT NOT NULL DEFAULT 'ai_assisted'
        CHECK(summary_origin IN ('ai_assisted', 'human')),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(review_status IN ('pending', 'verified', 'rejected')),
    pipeline_status TEXT NOT NULL DEFAULT 'not_run'
        CHECK(pipeline_status IN ('not_run', 'failed', 'completed')),
    reviewer TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK(review_status <> 'verified' OR length(trim(coalesce(reviewer, ''))) > 0)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) > 0)
);

CREATE TABLE IF NOT EXISTS note_tags (
    note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (note_id, tag_id)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY,
    input_file TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK(record_count >= 0),
    inserted_count INTEGER NOT NULL CHECK(inserted_count >= 0),
    skipped_count INTEGER NOT NULL CHECK(skipped_count >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK(record_count = inserted_count + skipped_count)
);

CREATE TABLE IF NOT EXISTS processing_logs (
    id INTEGER PRIMARY KEY,
    note_id TEXT REFERENCES notes(id),
    batch_id INTEGER REFERENCES import_batches(id),
    process_type TEXT NOT NULL CHECK(process_type IN ('import', 'annotation', 'glm')),
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed', 'not_run')),
    provider TEXT,
    model TEXT,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK(process_type <> 'glm' OR status <> 'succeeded'
        OR (length(trim(coalesce(provider, ''))) > 0
            AND length(trim(coalesce(model, ''))) > 0))
);

CREATE INDEX IF NOT EXISTS idx_sources_author ON sources(external_author);
CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_id_source ON notes(id,source_id);
CREATE INDEX IF NOT EXISTS idx_notes_split ON notes(split);
CREATE INDEX IF NOT EXISTS idx_checks_source_date ON source_checks(source_id, checked_on);
CREATE INDEX IF NOT EXISTS idx_annotations_category ON annotations(category);
CREATE INDEX IF NOT EXISTS idx_note_tags_tag ON note_tags(tag_id);

-- Editing labels/summaries must not silently rewrite the collected source excerpt.
CREATE TRIGGER IF NOT EXISTS notes_immutable_original
BEFORE UPDATE OF original_excerpt, original_sha256, source_id, excerpt_locator ON notes
WHEN NEW.original_excerpt IS NOT OLD.original_excerpt
    OR NEW.original_sha256 IS NOT OLD.original_sha256
    OR NEW.source_id IS NOT OLD.source_id
    OR NEW.excerpt_locator IS NOT OLD.excerpt_locator
BEGIN
    SELECT RAISE(ABORT, 'original excerpt and provenance are immutable');
END;

CREATE TRIGGER IF NOT EXISTS source_checks_immutable
BEFORE UPDATE ON source_checks
BEGIN
    SELECT RAISE(ABORT, 'source checks are append-only; create a new check id');
END;

CREATE TRIGGER IF NOT EXISTS source_checks_no_delete
BEFORE DELETE ON source_checks
BEGIN
    SELECT RAISE(ABORT, 'source checks are append-only; preserve previous evidence');
END;

CREATE TRIGGER IF NOT EXISTS sources_immutable_attribution
BEFORE UPDATE OF url, external_author ON sources
WHEN (NEW.url IS NOT OLD.url OR NEW.external_author IS NOT OLD.external_author)
    AND EXISTS(SELECT 1 FROM notes WHERE source_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'attribution of an imported source is immutable');
END;
