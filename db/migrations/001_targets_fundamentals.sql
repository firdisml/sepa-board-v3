-- Migration 001: targets, fundamentals, reasoning
-- Run this once in Supabase SQL Editor (your DB already has the base schema).

ALTER TABLE candidates ADD COLUMN IF NOT EXISTS target_2r    numeric;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS target_3r    numeric;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS fundamentals jsonb;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS reasoning    text;
