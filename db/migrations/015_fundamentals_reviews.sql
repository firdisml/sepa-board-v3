-- Quarterly fundamentals (computed by scanner/fundamentals.py, code not AI)
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS fundamentals jsonb;

-- Weekly AI review of the receipts/backtests (scanner/reviewer.py)
CREATE TABLE IF NOT EXISTS ai_reviews (
    id          serial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    review      jsonb NOT NULL
);
