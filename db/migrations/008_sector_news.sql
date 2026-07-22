-- Sector-rotation news headlines (the "why" behind sector moves), stored per run
ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS sector_news jsonb NOT NULL DEFAULT '[]'::jsonb;
