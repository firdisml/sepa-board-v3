-- Factor validation (PLAN §9 upgrade C / NEXT.md §3): do the board's scores
-- actually predict forward returns? Weekly job (scanner/factors.py) buckets
-- historical candidates by each score and stores the 20-day forward return
-- per bucket. One row set per computed_at run; /performance reads the latest.
CREATE TABLE IF NOT EXISTS factor_deciles (
    id           bigserial PRIMARY KEY,
    computed_at  timestamptz NOT NULL DEFAULT now(),
    market       text NOT NULL,
    factor       text NOT NULL,          -- quality | rs_rank | grade | anticipation
    bucket       text NOT NULL,          -- 'D1'..'D10' or 'A'..'E'
    n            int  NOT NULL,          -- candidate rows in the bucket
    fwd20_mean   numeric,                -- mean 20-session forward return, %
    fwd20_median numeric,
    win_rate     numeric,                -- % of rows with positive fwd20
    monotone     boolean                 -- whole-factor verdict, repeated per row
);
CREATE INDEX IF NOT EXISTS factor_deciles_latest_idx
    ON factor_deciles (factor, computed_at DESC);
