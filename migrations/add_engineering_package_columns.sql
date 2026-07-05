-- REQ-W27-002: Additive migration for WeeklyExport engineering package columns
-- Safe to run on production database with existing rows.
-- All new columns are nullable — existing rows remain valid with NULL values.
-- No existing column is altered, renamed, or removed.
--
-- Run BEFORE deploying the updated Brain Ops application.
-- Migration is idempotent: wrapped in BEGIN/COMMIT with error handling.
-- If a column already exists, SQLite returns "duplicate column name" which is
-- caught by the shell wrapper. Run all statements independently to be safe.
--
-- Usage:
--   sqlite3 btc_brain_ops.db < add_engineering_package_columns.sql
-- or via Railway CLI:
--   railway run sqlite3 $DB_PATH < add_engineering_package_columns.sql

-- Engineering Package content fields
ALTER TABLE weeklyexport ADD COLUMN engineering_index_content   TEXT;
ALTER TABLE weeklyexport ADD COLUMN timeline_content            TEXT;
ALTER TABLE weeklyexport ADD COLUMN event_bundle_json           TEXT;
ALTER TABLE weeklyexport ADD COLUMN runtime_stats_json          TEXT;
ALTER TABLE weeklyexport ADD COLUMN eo_register_content         TEXT;
ALTER TABLE weeklyexport ADD COLUMN er_register_content         TEXT;
ALTER TABLE weeklyexport ADD COLUMN engineering_summary_content TEXT;

-- Engineering Package versioning fields (Architecture Decision B/C)
ALTER TABLE weeklyexport ADD COLUMN package_version    TEXT;
ALTER TABLE weeklyexport ADD COLUMN schema_version     TEXT;
ALTER TABLE weeklyexport ADD COLUMN generator_version  TEXT;
ALTER TABLE weeklyexport ADD COLUMN compatible_runtime TEXT;

-- Verify
SELECT
    name,
    type
FROM pragma_table_info('weeklyexport')
ORDER BY cid;
