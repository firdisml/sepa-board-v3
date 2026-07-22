-- Migration 002: chart candles, position price tracking; fundamentals dropped
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS candles jsonb NOT NULL DEFAULT '[]';
ALTER TABLE candidates DROP COLUMN IF EXISTS fundamentals;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS last_price numeric;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS last_price_date date;
