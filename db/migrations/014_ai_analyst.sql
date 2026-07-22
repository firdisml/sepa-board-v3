-- AI analyst output: morning brief per run, risk note per candidate.
-- Written by scanner/analyst.py (chained workflow) AFTER the scan saves,
-- so both columns are nullable and the dashboard renders fine without them.
ALTER TABLE scan_runs  ADD COLUMN IF NOT EXISTS ai_brief jsonb;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS ai_note  jsonb;
