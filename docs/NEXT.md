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

**Expect the numbers to get WORSE. That is the point.** — see the corrected
table below; the FIRST attempt at this comparison was itself invalid.
2026-07-27/28. Every tactic re-measured on the fair basis (5y, full universe,
delisted included). The ranking did not survive:

| tactic          | exp   |   n | win% |   PF | CAGR%  | maxDD%  |
|-----------------|-------|-----|------|------|--------|---------|
| breakout        | +0.33 | 223 | 32.3 | 1.56 | +10.26 | -35.43  |
| buyable_gap_up  | +0.30 | 130 | 32.3 | 1.52 |  +6.02 | **-17.79** |
| ma20_bounce     | +0.20 | 225 | 31.1 | 1.40 |  +5.91 | -34.40  |
| pocket_pivot    | +0.08 | 321 | 24.9 | 1.16 |  -0.23 | -45.13  |
| ma50_bounce     | -0.01 | 432 | 22.0 | 0.98 |  -6.27 | -52.45  |
| episodic_pivot  | -0.38 | 723 | 17.7 | 0.48 | -48.20 | **-96.25** |

**ma20_bounce was never the best tactic.** Its apparent +0.53R was an artifact
of a 13-month survivorship-biased window inside one green regime — exactly the
shape a 20MA-bounce flatters itself in. On the fair basis it is THIRD at
+0.20R, and breakout leads at +0.33R.

buyable_gap_up is the risk-adjusted standout: within 0.03R of breakout for
HALF the drawdown (-17.8% vs -35.4%). episodic_pivot's -96% drawdown removes
any remaining doubt about its hazard status.

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

## 3. Factor validation (§9 upgrade C) — DONE 2026-07-26

Shipped: scanner/factors.py (quintiles, not deciles — young board), migration
018_factor_deciles, chained before reviewer in weekly-review.yml
(continue-on-error), FactorPanel on /performance with the NOT-monotone callout.
First real numbers land next Sunday run.

### original spec

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

## 4. Monthly heatmap + drawdown table — DONE 2026-07-26

Shipped: compute_stats now emits monthly_returns (year x month %, jsonb) and
drawdown_periods (5 worst); /backtest renders both above the equity curve.
Older saved runs lack the fields and the page skips them cleanly.

### original spec

`quantstats` is already installed and `compute_stats` already imports it.
Add `qs.stats.monthly_returns(ret)` → jsonb plus a drawdown-periods table, and
render on `/backtest` beside the existing bootstrap panel.

---

## 5. Announcements history: 5 → 30 per counter — DONE 2026-07-26

The 'third pattern' was already solved: klse_client.parse_feed handles
`a.announcement-item` (captured fixture, tested) and backfill_news.py does
deep dispatch pulls. The actual gap was the nightly scan persisting only the
5 sidebar items — scan.py now also pulls announcements_feed page 1 (30
items) per FETCHED counter (budget-gated, one small request) into
counter_news. Filings immutable, item_id PK dedupes.

### original spec

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

## Reversal strategies: MEASURED AND REJECTED (2026-07-27)

The board had no reversal tactic — every entry was trend-continuation gated on
the Trend Template, so a stock is invisible until AFTER its first leg (30% off
the low, within 25% of the high). Two reversal tactics were built and measured
on the fair basis (5y, full universe, delisted incl.) with an exit rule
corrected to be fair to them:

| tactic         | exp   |   n | win% |   PF | CAGR%  | maxDD% |
|----------------|-------|-----|------|------|--------|--------|
| ma200_reclaim  | -0.14 | 415 | 19.8 | 0.75 | -15.87 | -64.28 |
| undercut_rally | -0.45 | 657 | 22.1 | 0.36 | -49.87 | -96.99 |

**Both rejected.** Neither clears +0.25R, and both breach the -40% drawdown
limit badly. Not on the board, not in any rotation.

A methodological note worth keeping: the FIRST measurement was invalid. The
"close below the 50MA" exit fired on the entry bar for reversal entries (which
sit below the 50MA by nature), so undercut_rally held 2.3 days and exited 75%
of the time via that rule — measuring "enter and immediately exit". The exit
now arms only once price has closed above the 50MA during the trade, a strict
no-op for every trend-gated tactic. Re-measured fairly, undercut_rally got
WORSE (-0.27 -> -0.45): the premature exit had been cutting its losses.

**The pattern across all eight tactics is now unambiguous:**

    trend-gated      breakout +0.33, buyable_gap_up +0.30,
                     ma20_bounce +0.20, pocket_pivot +0.08     ALL POSITIVE
    not trend-gated  ma50_bounce -0.01, ma200_reclaim -0.14,
                     episodic_pivot -0.35, undercut_rally -0.45  ALL NEGATIVE

Perfect separation, and expectancy correlates INVERSELY with trade count
(223 trades -> +0.33R; 657 -> -0.45R). On Bursa the Trend Template gate is not
a limitation on the strategy — it IS the edge. Buying anything not already in
a confirmed uptrend has lost money in every form tested.

Do not re-attempt counter-trend entries here without evidence that overturns
this. Still open and NOT covered by the above: Weinstein Stage 1->2 transition
(requires the MA to be TURNING UP, so it is trend-initiation rather than
counter-trend) and catalyst-gated episodic_pivot (tests whether EP fails for
lack of a catalyst filter rather than being inherently broken).

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
