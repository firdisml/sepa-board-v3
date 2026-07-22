-- Trading dashboard schema (Postgres / Supabase / Neon)

CREATE TABLE IF NOT EXISTS scan_runs (
    id          serial PRIMARY KEY,
    run_date    date UNIQUE NOT NULL,
    regime      jsonb NOT NULL,          -- market regime traffic light + values
    status      text NOT NULL DEFAULT 'complete',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidates (
    id          serial PRIMARY KEY,
    run_id      int NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    ticker      text NOT NULL,
    bucket      text NOT NULL CHECK (bucket IN ('swing','position','watchlist','forming')),
    market      text NOT NULL DEFAULT 'US',
    rs_rank     int NOT NULL,
    price       numeric NOT NULL,
    pivot       numeric,
    stop        numeric,
    sector      text,
    extended    boolean NOT NULL DEFAULT false,
    checks      jsonb NOT NULL,          -- 8 trend template rules with values
    vcp         jsonb NOT NULL,          -- contraction sequence, dry-up, pivot
    extension   jsonb NOT NULL,          -- pct above pivot / 50MA
    earnings    jsonb,                   -- next earnings date + high_risk flag
    news        jsonb NOT NULL DEFAULT '[]',
    target_2r   numeric,                 -- entry + 2x risk (profit-taking level)
    target_3r   numeric,
    reasoning   text,                    -- plain-English explanation
    candles     jsonb NOT NULL DEFAULT '[]', -- last ~130 daily bars for charts
    levels      jsonb NOT NULL DEFAULT '{}', -- support/resistance with strength
    adr_pct     numeric,                 -- average daily range % (movement)
    quality     int,                     -- breakout setup quality 0-100
    industry    text,
    group_rs    int,                     -- industry group RS percentile 1-99
    patterns    jsonb NOT NULL DEFAULT '{}', -- candlestick/volume analysis
    setup       jsonb NOT NULL DEFAULT '{}', -- early-detection progress + flags
    name        text,                        -- company name
    reasoning_sections jsonb NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_candidates_ticker ON candidates(ticker);

CREATE TABLE IF NOT EXISTS sector_ranks (
    id              serial PRIMARY KEY,
    run_id          int NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    etf             text NOT NULL,
    sector          text NOT NULL,
    rank            int NOT NULL,
    rs_raw          numeric NOT NULL,
    mom_1m_pct      numeric NOT NULL,
    mom_3m_pct      numeric NOT NULL,
    rel_mom_1m_pct  numeric NOT NULL,
    rel_mom_3m_pct  numeric NOT NULL,
    quadrant        text NOT NULL,       -- leading / weakening / improving / lagging
    rotating_in     boolean NOT NULL,
    rotating_out    boolean NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sector_ranks_run ON sector_ranks(run_id);

-- Trade journal (manual entry in v1; moomoo sync later via adapter)
CREATE TABLE IF NOT EXISTS positions (
    id          serial PRIMARY KEY,
    ticker      text NOT NULL,
    entry_date  date NOT NULL,
    entry_price numeric NOT NULL,
    stop_price  numeric NOT NULL,
    shares      int NOT NULL,
    pivot       numeric,
    notes       text,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    exit_date   date,
    exit_price  numeric,
    is_paper    boolean NOT NULL DEFAULT false,
    last_price  numeric,
    last_price_date date,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Daily exit-signal evaluations for open positions
CREATE TABLE IF NOT EXISTS position_signals (
    id          serial PRIMARY KEY,
    position_id int NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    run_date    date NOT NULL,
    signals     jsonb NOT NULL,          -- exit rules with triggered flags + values
    UNIQUE (position_id, run_date)
);

CREATE TABLE IF NOT EXISTS watchlist (
    ticker      text PRIMARY KEY,
    added_at    timestamptz NOT NULL DEFAULT now(),
    notes       text
);

CREATE TABLE IF NOT EXISTS settings (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL
);

INSERT INTO settings (key, value) VALUES
    ('account_size_usd', '10000'),
    ('risk_per_trade_pct', '1.0'),
    ('rs_rank_threshold', '70'),
    ('min_price', '10'),
    ('min_dollar_volume', '5000000')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS ticker_meta (
    ticker     text PRIMARY KEY,
    industry   text,
    sector     text,
    name       text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
