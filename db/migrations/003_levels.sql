-- Migration 003: support/resistance levels per candidate
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS levels jsonb NOT NULL DEFAULT '{}';
