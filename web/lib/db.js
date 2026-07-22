import postgres from "postgres";
import { unstable_cache } from "next/cache";

let sql;
export function db() {
  if (!sql) {
    sql = postgres(process.env.DATABASE_URL, { ssl: "require", prepare: false, max: 3 });
  }
  return sql;
}

const cachedBundle = (runId) =>
  unstable_cache(
    async () => {
      const runs = await db()`SELECT * FROM scan_runs WHERE id = ${runId}`;
      const run = runs[0];
      let candidates;
      try {
        candidates = await db()`
          SELECT ticker, name, market, bucket, rs_rank, group_rs, price, pivot, stop,
                 sector, industry, extended, checks, vcp, extension, setup, quality,
                 adr_pct, target_2r, target_3r, earnings, fundamentals
          FROM candidates WHERE run_id = ${runId}
          ORDER BY quality DESC NULLS LAST, rs_rank DESC`;
      } catch {
        // new columns are created by scanner-side migrations, which land AFTER
        // the web deploy — fall back rather than 500 the homepage in between
        candidates = await db()`
          SELECT ticker, name, market, bucket, rs_rank, group_rs, price, pivot, stop,
                 sector, industry, extended, checks, vcp, extension, setup, quality,
                 adr_pct, target_2r, target_3r, earnings
          FROM candidates WHERE run_id = ${runId}
          ORDER BY quality DESC NULLS LAST, rs_rank DESC`;
      }
      return JSON.parse(JSON.stringify({ run, candidates }));
    },
    ["sepa-bundle", String(runId)], { revalidate: 300 }
  )();

export async function latestBundle() {
  const runs = await db()`SELECT id FROM scan_runs ORDER BY run_date DESC LIMIT 1`;
  if (!runs[0]) return null;
  return cachedBundle(runs[0].id);
}

export async function candidateDetail(ticker) {
  const rows = await db()`
    SELECT c.*, r.run_date AS as_of
    FROM candidates c JOIN scan_runs r ON r.id = c.run_id
    WHERE c.ticker = ${ticker}
    ORDER BY r.run_date DESC LIMIT 1`;
  return rows[0] ? JSON.parse(JSON.stringify(rows[0])) : null;
}

export async function signalOutcomes() {
  const rows = await db()`
    SELECT signal_date, ticker, market, signal_type, trigger_price, stop_price,
           target_price, triggered, outcome, r_multiple, days_to_trigger
    FROM signal_outcomes ORDER BY signal_date ASC`;
  return JSON.parse(JSON.stringify(rows));
}

export async function backtests() {
  const rows = await db()`
    SELECT id, created_at, label, params, stats, equity
    FROM backtests ORDER BY created_at DESC LIMIT 20`;
  return JSON.parse(JSON.stringify(rows));
}

export async function latestBacktestStatsByMarket() {
  // newest backtest per market — feeds the expectancy/avg-win lines on stock
  // charts; rows saved before the per-market split (no params.market) are skipped
  const rows = await db()`
    SELECT DISTINCT ON (params->>'market')
           params->>'market' AS market, id, label, created_at, stats
    FROM backtests
    WHERE params->>'market' IS NOT NULL
    ORDER BY params->>'market', created_at DESC`;
  const out = {};
  for (const r of rows) out[r.market] = JSON.parse(JSON.stringify(r));
  return out;
}

export async function latestReview() {
  try {
    const rows = await db()`
      SELECT created_at, review FROM ai_reviews ORDER BY created_at DESC LIMIT 1`;
    return rows[0] ? JSON.parse(JSON.stringify(rows[0])) : null;
  } catch {
    return null; // table appears with migration 015 — page must not 500 before that
  }
}

export async function backtestById(id) {
  const rows = await db()`SELECT * FROM backtests WHERE id = ${id}`;
  return rows[0] ? JSON.parse(JSON.stringify(rows[0])) : null;
}
