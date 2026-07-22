-- Migration 004: quality score, ADR%, industry group RS, ticker metadata cache, time stop
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS adr_pct  numeric;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS quality  int;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS industry text;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS group_rs int;

CREATE TABLE IF NOT EXISTS ticker_meta (
    ticker     text PRIMARY KEY,
    industry   text,
    sector     text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
