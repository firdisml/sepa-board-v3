# Next tasks — start here

Written 2026-07-25. Everything below is specified enough to execute without
re-deriving anything. Read `PLAN.md` §9 and §12.1 for the surrounding context.

---

## 1. Backtest: fix survivorship + extend to 5 years  ← DONE 2026-07-25/26

Shipped as `--deep-history` (backtest.py `load_deep_history`). Results on the
fair basis (5y, full universe, delisted included, RS ranked on all ~1,062):
breakout +0.33R / CAGR 10.3% / DD -35.4% (id=7); buyable_gap_up +0.30R /
DD -17.8% (id=9, NEW strategy); pocket_pivot +0.08R / DD -45.1% (id=8, NEW,
weak — do not promote). ma20/ma50/EP still await deep-history re-measurement.

Findings recorded while doing it:
- The gotcha was real at scale: EODHD flags **97 live blue-chips** (AEON,
  AXIATA, BAT, BURSA…) as "delisted" under stale alphabetic alias codes. The
  numeric-code filter catches them; `load_deep_history` also excludes any
  "delisted" ticker whose last bar is <=15 days old, and logs both.
- **Census (2026-07-26, public sources):** Bursa ~987 listed (2023) →
  ~1,072 (2025); ~200+ IPOs over 2021-2025 vs net +~130 ⇒ implies roughly
  **80-100 genuine delistings in 5y**. EODHD serves 112 delisted numeric-code
  names ⇒ the delisted directory is plausibly near-complete, NOT badly thin.
  Residual risk: the ~58 per-run fetch failures — failures are now logged by
  name with delisted/live split and reason classes; check the next run's log
  to see whether the failed names skew delisted (real survivorship holes).
- The liquidity pre-filter sketched in the original spec was WRONG — it
  shrank the RS-rank pool to ~230 names and inflated results (+0.88R). Fetch
  the whole directory; RS must rank the full universe (scan.py's own rule).
- CI now caches `.cache/deep_history` per ISO week (actions/cache), so
  same-week re-runs cost ~0 API calls.

---

## 1b. (superseded original spec, kept for context)

**Why:** the current backtest reads `warehouse.load_window`, which holds only
**currently listed** counters on a rolling ~420-session window (§3.3). Two
consequences, both material:

- Every counter that collapsed and delisted over the period is **invisible**.
  That is precisely the survivorship bias §9 upgrade A exists to remove, and it
  is NOT yet removed despite the runs being labelled "full universe".
- The tradeable span is ~13 months, not the 2 years the `--years` flag implies:
  the Trend Template needs 200+ bars of warm-up, so no signal can fire before
  roughly mid-2025. All 81 ma20_bounce trades landed 2025-06-16 → 2026-07-17,
  entirely inside a **green regime**. The strategy has never been tested in a
  Bursa downtrend, and a 20MA-bounce is exactly the shape that flatters itself
  in an uptrend.

**Implementation** — add `--deep-history` alongside `--universe` in
`scanner/backtest.py`:

```python
# The warehouse cannot answer multi-regime questions by design. Pull on demand.
syms = eod.symbols("KLSE", include_delisted=True)   # ~1,082 live + delisted
# 1. liquidity-filter ONCE using the warehouse's 20d value (cheap)
# 2. history() each survivor for `years` (EODHD carries 30y)
# 3. feed that dict into run_per_market() exactly as load_window's output is fed
```

Two known gotchas:

- `eodhd_client.symbols()` currently **drops non-numeric KLSE codes** (added to
  kill the HEXTAR/HLIND/ICON/KLCC aliases). Delisted counters may use different
  code formats — verify the filter does not silently drop the very rows this
  task exists to include.
- **Cache to parquet** keyed on `ticker+years`, or every re-run costs another
  ~130 API calls. `.gitignore` already covers `*.parquet`.

**Expect the numbers to get WORSE. That is the point.** If ma20_bounce's
+0.53R expectancy survives delisted inclusion and a downtrend, it can be
believed. Current results to beat (PLAN §12.1): ma20_bounce +0.53R,
breakout +0.25R, ma50_bounce -0.19R, episodic_pivot -0.38R.

---

## 2. Winner table at the top of /backtest

New `web/components/BacktestWinner.js`, rendered **above** the stats row in
`web/app/backtest/page.js`.

- `lib/db.js` already exposes `backtests()` (newest 20 rows).
- Group by `params->>'strategy'`, take the newest row per strategy, rank by
  `stats.expectancy_r`.
- Mark row one `WINNER`.
- Reuse `components/StrategyBoard.js`'s shape — the `.bt` table CSS, `ta-l`/
  `ta-r` alignment classes and `pos`/`neg` number colours all exist already.
- A strategy with a negative expectancy should carry the `neg edge` tag, same
  as StrategyBoard does.

---

## 3. Factor validation (§9 upgrade C)

New `scanner/factors.py`, chained to `weekly-review.yml` (weekly question, not
nightly).

- Join historical `candidates` rows to warehouse bars.
- For `quality`, `rs_rank`, `fundamentals->>'grade'`,
  `setup->anticipation->>'score'`: bucket into deciles, compute 20-day forward
  return per decile.
- Store to a new `factor_deciles` table; render per factor on `/performance`.
- **If a score's deciles do not slope monotonically, say so on the page.** A
  quality score that does not predict is worse than no score, because it looks
  like information.

---

## 4. Monthly heatmap + drawdown table

`quantstats` is already installed and `compute_stats` already imports it.
Add `qs.stats.monthly_returns(ret)` → jsonb plus a drawdown-periods table, and
render on `/backtest` beside the existing bootstrap panel.

---

## 5. Announcements history: 5 → 30 per counter

`klsescreener.com/v2/announcements/stock/{code}` serves **30** announcements
(verified 2026-07-23); the scan currently takes only the 5 embedded in the
stock-page sidebar.

The markup is a **third pattern** — neither `<table>` (so `read_html` misses it,
`<tr>` count is 0) nor `<li class="list-group-item">` (the sidebar's shape).
Dump the markup around a `/v2/announcements/view/NNN` link to find the wrapper
element. `classify()` and `street_cache` already exist, so this is parse +
cache with a long TTL — filings are immutable once made.

Unlocks the §7.1 question: *was there a contract win inside the base?*

---

## Open judgement calls

- **`ma50_bounce` is still a full setup** despite -0.19R over 151 trades. EP was
  demoted because -0.38R was unambiguous; ma50 is milder and one window is thin
  evidence. Revisit once receipts accumulate.
- **`swing: 0` every night so far.** Not a bug — the ADR ≥ 1.5% floor demotes
  the one 8/8 counter that reaches a pivot (2658.KL, ADR 0.41%). If it stays
  zero across a week of real trading, that threshold deserves review.

## Housekeeping

Rotate the EODHD token and the Vercel `DASHBOARD_PASSWORD` — both passed
through a chat transcript in plaintext.
