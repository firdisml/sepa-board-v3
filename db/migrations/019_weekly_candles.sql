-- Weekly OHLC for the dossier chart toggle. A base runs 7-65 weeks, which is
-- 35-325 daily candles — the shape Weinstein and Minervini actually teach on
-- is only legible weekly. Carried alongside the daily series rather than
-- resampled in the browser so the no-lookahead rule stays in ONE place
-- (scanner/weekly.py drops the in-progress week; a client-side resample would
-- quietly reintroduce a partial final bar).
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS candles_weekly jsonb NOT NULL DEFAULT '[]';
