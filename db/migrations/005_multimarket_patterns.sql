-- Migration 005: multi-market (US + Bursa), forming bucket, patterns, setup progress
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS market   text NOT NULL DEFAULT 'US';
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS patterns jsonb NOT NULL DEFAULT '{}';
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS setup    jsonb NOT NULL DEFAULT '{}';
ALTER TABLE candidates DROP CONSTRAINT IF EXISTS candidates_bucket_check;
ALTER TABLE candidates ADD CONSTRAINT candidates_bucket_check
    CHECK (bucket IN ('swing','position','watchlist','forming'));
ALTER TABLE positions ADD COLUMN IF NOT EXISTS last_price numeric;
