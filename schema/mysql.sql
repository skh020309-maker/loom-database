-- MySQL 8.0.29+ reference schema. NOT executed against a MySQL server.
-- Select an empty target database before running this file in the mysql client.
-- Idempotent creation does not upgrade incompatible pre-existing tables/triggers.
-- utf8mb4_0900_bin preserves case and trailing-space distinctions like SQLite.
-- Indexed text is bounded: sources.url 768 chars; note IDs/tag names 191 chars.
-- export_mysql.py rejects values outside these bounds instead of truncating.
-- ISO-8601 date/time values remain text so the original SQLite values survive.
-- https://dev.mysql.com/doc/refman/8.0/en/create-trigger.html
-- https://dev.mysql.com/doc/refman/8.0/en/data-type-defaults.html
SET NAMES utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS sources (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    url VARCHAR(768) NOT NULL UNIQUE,
    external_author LONGTEXT NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    license_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
    license_name LONGTEXT,
    license_url LONGTEXT,
    created_at LONGTEXT NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    CONSTRAINT ck_sources_url CHECK (CHAR_LENGTH(TRIM(url)) > 0),
    CONSTRAINT ck_sources_author CHECK (CHAR_LENGTH(TRIM(external_author)) > 0),
    CONSTRAINT ck_sources_kind CHECK (source_kind = 'public_note'),
    CONSTRAINT ck_sources_license CHECK (license_status IN ('unknown', 'open')),
    INDEX idx_sources_author (external_author(191))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS notes (
    id VARCHAR(191) NOT NULL PRIMARY KEY,
    source_id BIGINT NOT NULL,
    title LONGTEXT NOT NULL,
    language VARCHAR(32) NOT NULL,
    original_excerpt LONGTEXT NOT NULL,
    original_sha256 VARCHAR(64) NOT NULL UNIQUE,
    excerpt_locator LONGTEXT NOT NULL,
    retrieved_on LONGTEXT NOT NULL,
    content_origin VARCHAR(32) NOT NULL DEFAULT 'public_excerpt',
    split VARCHAR(32) NOT NULL,
    training_allowed BIGINT NOT NULL DEFAULT 0,
    owner_user_id LONGTEXT,
    created_at LONGTEXT NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    CONSTRAINT fk_notes_source FOREIGN KEY (source_id) REFERENCES sources(id),
    CONSTRAINT ck_notes_id CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    CONSTRAINT ck_notes_title CHECK (CHAR_LENGTH(TRIM(title)) > 0),
    CONSTRAINT ck_notes_language CHECK (language IN ('en', 'zh')),
    CONSTRAINT ck_notes_excerpt CHECK (
        CHAR_LENGTH(TRIM(original_excerpt)) > 0 AND CHAR_LENGTH(original_excerpt) <= 500),
    CONSTRAINT ck_notes_hash CHECK (
        CHAR_LENGTH(original_sha256) = 64 AND original_sha256 NOT REGEXP '[^0-9a-f]'),
    CONSTRAINT ck_notes_locator CHECK (CHAR_LENGTH(TRIM(excerpt_locator)) > 0),
    CONSTRAINT ck_notes_origin CHECK (content_origin = 'public_excerpt'),
    CONSTRAINT ck_notes_split CHECK (split IN ('dev', 'test')),
    CONSTRAINT ck_notes_training CHECK (training_allowed IN (0, 1)),
    INDEX idx_notes_source (source_id),
    INDEX idx_notes_split (split)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS annotations (
    note_id VARCHAR(191) NOT NULL PRIMARY KEY,
    summary_zh LONGTEXT NOT NULL,
    category VARCHAR(32) NOT NULL,
    summary_origin VARCHAR(32) NOT NULL DEFAULT 'ai_assisted',
    review_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    pipeline_status VARCHAR(32) NOT NULL DEFAULT 'not_run',
    reviewer LONGTEXT,
    updated_at LONGTEXT NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    CONSTRAINT fk_annotations_note FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
    CONSTRAINT ck_annotations_summary CHECK (CHAR_LENGTH(TRIM(summary_zh)) > 0),
    CONSTRAINT ck_annotations_category CHECK (category IN ('question', 'idea', 'note', 'event')),
    CONSTRAINT ck_annotations_origin CHECK (summary_origin IN ('ai_assisted', 'human')),
    CONSTRAINT ck_annotations_review CHECK (review_status IN ('pending', 'verified', 'rejected')),
    CONSTRAINT ck_annotations_pipeline CHECK (pipeline_status IN ('not_run', 'failed', 'completed')),
    CONSTRAINT ck_annotations_reviewer CHECK (
        review_status <> 'verified' OR CHAR_LENGTH(TRIM(COALESCE(reviewer, ''))) > 0),
    INDEX idx_annotations_category (category)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS tags (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(191) NOT NULL UNIQUE,
    CONSTRAINT ck_tags_name CHECK (CHAR_LENGTH(TRIM(name)) > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS note_tags (
    note_id VARCHAR(191) NOT NULL,
    tag_id BIGINT NOT NULL,
    PRIMARY KEY (note_id, tag_id),
    CONSTRAINT fk_note_tags_note FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
    CONSTRAINT fk_note_tags_tag FOREIGN KEY (tag_id) REFERENCES tags(id),
    INDEX idx_note_tags_tag (tag_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS import_batches (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    input_file LONGTEXT NOT NULL,
    input_sha256 LONGTEXT NOT NULL,
    record_count BIGINT NOT NULL,
    inserted_count BIGINT NOT NULL,
    skipped_count BIGINT NOT NULL,
    created_at LONGTEXT NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    CONSTRAINT ck_batches_record_count CHECK (record_count >= 0),
    CONSTRAINT ck_batches_inserted_count CHECK (inserted_count >= 0),
    CONSTRAINT ck_batches_skipped_count CHECK (skipped_count >= 0),
    CONSTRAINT ck_batches_total CHECK (record_count = inserted_count + skipped_count)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS processing_logs (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    note_id VARCHAR(191),
    batch_id BIGINT,
    process_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    provider LONGTEXT,
    model LONGTEXT,
    details LONGTEXT NOT NULL,
    created_at LONGTEXT NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    CONSTRAINT fk_logs_note FOREIGN KEY (note_id) REFERENCES notes(id),
    CONSTRAINT fk_logs_batch FOREIGN KEY (batch_id) REFERENCES import_batches(id),
    CONSTRAINT ck_logs_type CHECK (process_type IN ('import', 'annotation', 'glm')),
    CONSTRAINT ck_logs_status CHECK (status IN ('succeeded', 'failed', 'not_run')),
    CONSTRAINT ck_logs_glm CHECK (
        process_type <> 'glm' OR status <> 'succeeded'
        OR (CHAR_LENGTH(TRIM(COALESCE(provider, ''))) > 0
            AND CHAR_LENGTH(TRIM(COALESCE(model, ''))) > 0))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

DELIMITER //
CREATE TRIGGER IF NOT EXISTS notes_immutable_original
BEFORE UPDATE ON notes FOR EACH ROW
BEGIN
    IF NOT (NEW.original_excerpt <=> OLD.original_excerpt)
        OR NOT (NEW.original_sha256 <=> OLD.original_sha256)
        OR NOT (NEW.source_id <=> OLD.source_id)
        OR NOT (NEW.excerpt_locator <=> OLD.excerpt_locator) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'original excerpt and provenance are immutable';
    END IF;
END//

CREATE TRIGGER IF NOT EXISTS sources_immutable_attribution
BEFORE UPDATE ON sources FOR EACH ROW
BEGIN
    IF (NOT (NEW.url <=> OLD.url) OR NOT (NEW.external_author <=> OLD.external_author))
        AND EXISTS (SELECT 1 FROM notes WHERE source_id = OLD.id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'attribution of an imported source is immutable';
    END IF;
END//
DELIMITER ;
