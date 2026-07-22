-- The receipts: every past signal graded against what actually happened
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id              serial PRIMARY KEY,
    eval_date       date NOT NULL,
    signal_date     date NOT NULL,
    ticker          text NOT NULL,
    market          text,
    signal_type     text NOT NULL,          -- breakout / early_entry
    trigger_price   numeric,
    stop_price      numeric,
    target_price    numeric,
    triggered       boolean NOT NULL,
    outcome         text NOT NULL,          -- win / loss / open / never_triggered
    r_multiple      numeric,
    days_to_trigger int,
    UNIQUE (signal_date, ticker, signal_type)
);
CREATE INDEX IF NOT EXISTS idx_signal_outcomes_type ON signal_outcomes(signal_type);
