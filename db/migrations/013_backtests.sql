-- On-demand backtest results (scanner/backtest.py)
CREATE TABLE IF NOT EXISTS backtests (
    id          serial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    label       text,
    params      jsonb NOT NULL,          -- strategy + universe + risk settings
    stats       jsonb NOT NULL,          -- CAGR, Sharpe, maxDD, expectancy, PF...
    equity      jsonb NOT NULL,          -- [{t, eq}] daily equity curve
    trades      jsonb NOT NULL           -- full trade list (transparency)
);
