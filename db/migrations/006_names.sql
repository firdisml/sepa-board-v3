-- Migration 006: company names on candidates + ticker metadata cache
ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE ticker_meta ADD COLUMN IF NOT EXISTS name text;
