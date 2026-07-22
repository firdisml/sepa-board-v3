-- Migration 007: grouped reasoning sections
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS reasoning_sections jsonb NOT NULL DEFAULT '[]';
