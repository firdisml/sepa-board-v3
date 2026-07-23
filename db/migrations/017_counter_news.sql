-- Durable per-counter news + announcement history (PLAN §7.2). Sources: the
-- nightly dossier's embedded items (zero extra requests) and the paginated
-- KLSE Screener feeds (/v2/news/stock/{code}, /v2/announcements/stock/{code})
-- for dispatch-only deep backfill. item_id comes from the /view/{id} URL and
-- is the dedupe key. UPSERT, never DELETE (core value #3).
--
-- Headlines are UNTRUSTED third-party text and never enter a grade, bucket,
-- signal or receipt — consumers are EP-catalyst lookback in the AI notes,
-- the QR-filing cache-refresh trigger (category = 'results'), and the
-- stock-page news tab.
CREATE TABLE IF NOT EXISTS counter_news (
    ticker       text NOT NULL,               -- e.g. 5326.KL
    kind         text NOT NULL CHECK (kind IN ('news', 'announcement')),
    item_id      text NOT NULL,               -- id in the /view/ URL
    title        text NOT NULL,
    url          text,
    source       text,                        -- TheEdge/Sinchew/...; '' for filings
    category     text,                        -- classify() label, announcements only
    published_at timestamptz,                 -- parsed from <time datetime>; NULL if unparseable
    date_text    text,                        -- the raw date string, never lost
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, item_id)
);

CREATE INDEX IF NOT EXISTS counter_news_ticker_idx
    ON counter_news (ticker, kind, published_at DESC);
