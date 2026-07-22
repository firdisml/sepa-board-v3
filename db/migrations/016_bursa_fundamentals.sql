-- Bursa fundamentals + street cache, refreshed by the nightly scan via
-- klse_client (KLSE Screener). The v2 table shape is kept deliberately: the
-- pipeline speaks one fundamentals shape and only the client knows the source.
--
-- v3 refresh rule (PLAN §5): re-fetch a counter when its cache is >7 days old
-- AND it is on the board, or when a new QR filing appears in its
-- announcements. Cache older than 60 days is never served as current — the
-- stock page banners its age instead.
CREATE TABLE IF NOT EXISTS bursa_fundamentals (
    ticker     text PRIMARY KEY,          -- e.g. 1155.KL
    data       jsonb NOT NULL,            -- same shape fundamentals.from_dossier returns
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- The full dossier (announcements, news, dividends, shareholding) kept beside
-- it so a re-run reuses one fetch. Commentary only — street data never enters
-- a grade, bucket, signal or receipt (PLAN §7.1).
CREATE TABLE IF NOT EXISTS street_cache (
    ticker     text NOT NULL,
    page       text NOT NULL DEFAULT 'dossier',
    data       jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, page)
);
