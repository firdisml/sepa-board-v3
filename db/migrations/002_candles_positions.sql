-- Migration 002: chart candles, position price tracking.
-- HISTORICAL NOTE: this migration originally also ran
--   ALTER TABLE candidates DROP COLUMN IF EXISTS fundamentals;
-- because v2 dropped fundamentals here and re-added a restructured column in
-- migration 015. That is fine when migrations run ONCE. But apply_migrations
-- re-runs the whole set on every scan AND every analyst invocation, and
-- DROP COLUMN IF EXISTS is destructive on EVERY run — so this line silently
-- wiped all fundamentals grades each time the analyst started (the analyst
-- migrates, then only writes ai_note, so it never repopulated them). The scan
-- survived only because it re-enriches after migrating. The drop is removed:
-- 001 adds the column, 015 re-adds it (IF NOT EXISTS, a no-op), data preserved.
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS candles jsonb NOT NULL DEFAULT '[]';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS last_price numeric;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS last_price_date date;
