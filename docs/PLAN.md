# SEPA Board v3 — End-to-End Build Plan

**Status:** approved blueprint, not yet started.
**Owner:** firdisml. **Executor:** any AI coding session or human — this document is
self-contained; no prior conversation context is required.
**Predecessor:** github.com/firdisml/sepa-board (referred to below as "v2"). The v2
repo is the source of all ported code — read it before writing anything new.

---

## 1. Charter

> **SCOPE CHANGE (2026-07-22, owner decision): v3.0 is BURSA MALAYSIA ONLY.**
> US is parked, not deleted — the engine stays multi-market (market configs),
> so US reactivation later is configuration + a second cron, not a rebuild.
> Wherever this document says "US + MY", read "MY now, US later".
> Second decision: **Apify is dropped**; MY fundamentals + street data come
> from i3investor (code-parsed tables → mechanical grade; Gemini interprets).
> Prices remain EODHD (validated live 2026-07-22). yfinance is REMOVED from
> the stack entirely — its last jobs (US fundamentals/earnings) left with US.

A nightly end-of-day stock screener for the **Bursa Malaysia** market (US later)
implementing **Mark Minervini's SEPA methodology** (Trend Template, VCP, buy
points, risk-first position sizing, sell rules) with O'Neil/CANSLIM context
(RS ranking, industry groups, market regime, fundamentals), a set of
receipts-graded entry tactics, honest backtesting, and an AI commentary layer.
Dark trading-desk dashboard. Educational tool, not financial advice.

**One-line goal vs v2:** same rules, same honesty, better data, fewer machines.

### 1.1 Non-negotiable core values (violating any of these = wrong build)

1. **Minervini SEPA is the backbone.** Stage-2 uptrends only (8-rule Trend
   Template gate), VCP → pivot → volume-confirmed breakout as the primary
   entry, stop defined before entry and never wider than the rules allow,
   sell into strength, cut losses fast, no price predictions — R-multiple
   levels only.
2. **Code computes, AI interprets.** No LLM output ever enters a signal,
   stop, size, bucket, or the receipts. AI renders as clearly-badged
   commentary with a mechanical fallback.
3. **Receipts over claims.** Every published signal is graded against real
   subsequent bars. Never-triggered signals stay on the record. Losers are
   never deleted (no DELETE-and-regrade; upsert only). Delisted/dropped
   tickers keep their last grade.
4. **No lookahead, anywhere.** Baselines shifted so a day never sits in its
   own average; signals from day-t data fill at t+1's open; per-market
   calendars never mixed in one rolling window; stops checked on the entry
   bar; costs modeled per side; survivorship bias fixed via delisted data
   (new in v3) or explicitly disclosed where it can't be.
5. **Honest UI.** Every rule shows its computed numbers; stale data is
   loudly labeled; empty states never fake; AI content visibly tagged.

### 1.2 Non-goals

- No real-time / intraday anything. This is an EOD, before-market system.
- No options/warrants/derivatives. No shorting.
- No AI-driven stock scoring or ranking inside the mechanical funnel.
- No multi-user product features (single password gate is fine).

### 1.3 Budget (DECIDED — supersedes the old $30 figure): RM150/month ALL-IN hard cap
EODHD ~RM82 (owner-verified card price; All-World is the tier that carries
KLSE — no cheaper MY-only plan exists) + Gemini RM68 cap. MY-only scope
shrinks the AI load (~30-45 picks/night vs 90): expected burn ~RM30–45
with pro-class Tier-A notes from day one — roughly RM25/month of natural
headroom. After the validation month, switch EODHD to annual billing
(~RM60–66/month equivalent); freed money = buffer / portfolio-coach fund,
NOT new spending. Apify: removed. yfinance: removed.

| Line | Vendor | Est. |
|---|---|---|
| Prices MY (EOD bulk; US later, same plan) | EODHD "All-World" plan | ~RM82 |
| MY fundamentals + street data | i3investor (code-parsed, free) | RM0 |
| AI (notes/briefs/review + search) | Gemini API (search grounding free ≤5k/mo) | ~$7–9 |
| Compute | GitHub Actions (public/private repo minutes) + Supabase free + Vercel free | $0 |

---

## 2. Architecture

```
                    one-time backfill (~400 trading days, full universes)
                                        │
                                        ▼
   EODHD bulk EOD ──2 calls/night──► Postgres candle WAREHOUSE (Supabase)
                                        │
       ┌────────────────────────────────┬───────────────────────────────┐
       ▼                                ▼                               ▼
  12:30 UTC cron (Actions)        i3investor fetch (in-scan)      on demand
  scan MY (Bursa closed 09:00)    fundamentals + street pages     backtests
       │   [US cron parked — reactivation = config + cron]              │
       └──────── same engine: RS → Trend Template → VCP → tactics → buckets
                                        │
                              scan_runs + candidates (Postgres)
                                        │
             ┌──────────────────────────┼────────────────────────────┐
             ▼                          ▼                            ▼
     ai-analyst (chained)        backtest (chained)          weekly-review (cron Sun)
     Gemini notes/brief          per-market, per-strategy    receipts+backtest study
             │                          │                            │
             └──────────────► Next.js dashboard on Vercel ◄──────────┘
```

- **Everything runs on GitHub Actions.** No VPS, no moomoo OpenD. (The v2 VPS
  becomes optional/retired; moomoo institutional/whale-flow enrichment is
  dropped — see §14 Risks for the feature loss note.)
- Because the scans are Actions workflows again, `workflow_run` chaining works
  natively: ai-analyst and backtest trigger on scan success. Keep a
  **freshness guard** anyway (see §11.3).

### 2.1 Repo layout (new repo)

```
new-repo/
  scanner/
    eodhd_client.py      # NEW — the only new data code
    warehouse.py         # NEW — candle warehouse read/write + backfill
    scan.py              # ported from v2, data calls swapped to warehouse
    indicators.py        # ported VERBATIM from v2
    patterns.py          # ported VERBATIM
    reasoning.py         # ported VERBATIM
    performance.py       # ported VERBATIM (receipts)
    backtest.py          # ported + full-universe mode + bootstrap (§9)
    fundamentals.py      # rewritten thin: grade math kept, source = i3_client
    i3_client.py         # NEW — i3investor fetch+parse (fundamentals + street pages)
    analyst.py           # ported VERBATIM (Gemini)
    reviewer.py          # ported VERBATIM
    news.py              # slimmed: Gemini search grounding is the news source
    sectors.py           # ported VERBATIM
    db.py                # ported + warehouse DDL
    tests/               # port ALL v2 tests; they encode fixed bugs
  db/schema.sql + migrations/
  web/                   # ported Next.js 14 app, Bursa UI restored
  .github/workflows/     # scan-my.yml, ai-analyst.yml, backtest.yml,
                         # weekly-review.yml  (scan-us.yml parked for later)
  docs/THIS-PLAN.md
```

**Porting rule:** `indicators.py`, `patterns.py`, `reasoning.py`,
`performance.py`, `analyst.py`, `reviewer.py`, `sectors.py` and all tests move
verbatim — they encode dozens of already-fixed bugs (stale pivots, shifted
volume baselines, calendar NaN-poisoning, jsonb NaN crashes, thinking-token
budgets, Gemini model fallbacks + busy-model circuit breaker). Do not rewrite
what is already debugged. v2 git history is the reference for *why* each line
exists.

---

## 3. Data layer (the only genuinely new build)

### 3.1 EODHD facts

- Plan: **All-World** (~€19.99/mo). Includes: EOD daily bars for 60+
  exchanges (30y depth), splits/dividends (adjusted closes), bulk EOD (whole
  exchange per call), delayed live quotes (unused), exchange symbol
  directories **including delisted tickers**, FX + indices EOD.
  Limit: **100,000 API calls/day** (bulk EOD = 100 calls per request; verify
  current call-cost table at signup).
- NOT included (by design, don't buy): fundamentals tier, technical-indicator
  API, screener API, intraday. The engine computes its own indicators so the
  live scan and backtest share identical math.
- **Ticker format mapping** (critical): EODHD uses `SYMBOL.EXCHANGE` —
  `AAPL.US`, `1155.KLSE`. Internal/DB/web format stays as v2: bare `AAPL` for
  US, `1155.KL` for Bursa. Map ONLY at the client boundary:
  `AAPL ↔ AAPL.US`, `1155.KL ↔ 1155.KLSE`. Indices: `GSPC.INDX`/SPY.US as
  benchmark, `KLSE.INDX` for KLCI (verify exact index codes at signup).

### 3.2 `eodhd_client.py` contract

```python
def bulk_eod(exchange: str, date: str) -> pd.DataFrame
    # one call: every symbol's OHLCV + adjusted_close for that date
def history(ticker: str, years: int = 2) -> pd.DataFrame
    # yfinance-shaped: Open/High/Low/Close/Volume, DatetimeIndex, oldest first
    # USE adjusted prices consistently (splits!)
def symbols(exchange: str, include_delisted: bool = True) -> pd.DataFrame
    # symbol directory; keep type filter: common stock only (no ETFs/funds/warrants)
```
Retries with backoff on 5xx; hard-fail (abort scan) if bulk EOD for the run
date is missing — never scan on stale prices silently.

**Freshness gate (mandatory, both scans):** before scanning, assert the
ingested bulk data's bar date equals the market's expected session date
(from the trading calendar). If stale: retry after 30/60/90 min (the scan
cron has slack), and if still stale ABORT with a loud log line — a skipped
scan is recoverable, a silently day-late board is not. This rule exists
because Yahoo served Bursa EOD a day late routinely (v1 experience); the
gate makes ANY vendor's lag visible on day one instead of after months of
quietly degraded entries.

### 3.3 Candle warehouse

Table `candles(ticker text, d date, o,h,l,c numeric, v bigint,
PRIMARY KEY(ticker, d))`, plus `candles_meta(exchange, last_ingested date)`.

- **Rolling window: ~420 trading days** for the full universe (enough for
  260-bar Trend Template + 252-day RS + buffer). Nightly job: ingest today's
  bulk EOD, delete rows older than the window. Sizing: US ~6–8k common
  stocks + MY ~1k × 420 rows ≈ 3–4M rows — fits Supabase free tier; monitor.
- Deeper history (charts need only 130 bars — fine; backtests need years) is
  fetched per-ticker on demand via `history()`, NOT stored.
- **Backfill script** (one-time): pull `history()` for every current symbol
  of both exchanges (~9k calls — under one day's quota), write the window.
- **Split handling:** on any split detected (EODHD splits endpoint or daily
  adjusted-vs-raw mismatch), re-pull that ticker's history and overwrite its
  window rows. Un-adjusted stitching corrupts every MA — treat as P0 bug.

### 3.4 Universe rules (per scan)

- US: all common stocks from `symbols("US")`, then liquidity filter
  price ≥ $10 (env `SCAN_MIN_PRICE`), 20-day avg dollar-volume ≥ $5M
  (`SCAN_MIN_DOLLAR_VOL`). No max price.
- MY: all Bursa common stocks, price ≥ RM0.50, 20d avg value ≥ RM2M
  (`SCAN_MY_MIN_DOLLAR_VOL`), min ADR 1.5% for swing (`SCAN_MY_MIN_ADR`).
- **RS ranks are computed on the FULL universe BEFORE the liquidity filter**
  (v2 lesson: ranking survivors inflates every rank). v3 restores true
  full-universe RS for both markets (v2's moomoo funnel could only rank ~280).

---

## 4. Scanner pipeline (per market, per run)

session check (trading calendar; `SCAN_FORCE=1` override) → load warehouse
window → RS raw + percentile ranks (1–99) on full universe → liquidity filter
→ per ticker with rank ≥ 55: Trend Template → keep pass_all or near-miss
(≥ total−2) → build candidate (tactics below) → buckets with caps
swing 20 / position 30 / watchlist 20 / forming 15 → enrich (meta,
fundamentals, earnings, group RS, targets, reasoning) → candles payload
(last 130 bars + m20/m50/m150/m200 + RS line vs benchmark + Bollinger +
shifted v50) → save run → evaluate open journal positions → grade receipts.

### 4.1 Trend Template (8 rules; reduced IPO path for 126–259 bars)

1. price > 150MA and > 200MA;  2. 150MA > 200MA;  3. 200MA rising vs 22
sessions ago;  4. 50MA > 150MA and > 200MA;  5. price > 50MA;
6. price ≥ 30% above 52w low;  7. price within 25% of 52w high (intraday
High/Low based);  8. RS rank ≥ 70. IPO path: evaluate only rules whose
windows exist; carry `ipo=true`, `rules_total` = rules actually checked.

### 4.2 RS formula (IBD-weighted)

`RS_raw = 0.4·C/C63 + 0.2·C/C126 + 0.2·C/C189 + 0.2·C/C252`, weights
renormalized for young stocks (≥126 bars); percentile-rank per market 1–99.
Industry group RS: median member rank per group, percentile-ranked, ≥3 members.

### 4.3 VCP detection (heuristic, last 75 bars)

Swing highs/lows (5-bar window); each high pairs with the DEEPEST low before
the next high; contraction = depth% of each pullback; valid VCP =
≥2 contractions, last < first, last ≤ 12%, at most ONE out-of-order pair,
volume dry-up (last 5d avg < 60% of the 50d avg ending a week ago), pivot =
max of last 2 swing highs. Pivot nulled if price < pivot×0.80 (stale).

### 4.4 Tactics (each an object in `candidates.setup`; exact v2 rules)

| Tactic | Gate | Trigger conditions | Entry/stop |
|---|---|---|---|
| breakout | swing bucket | close ≥ pivot on vol > 1.4× prior-50d avg, or within 5% of pivot, not extended | pivot / max(10d swing low, entry−8%) |
| early_entry | base exists, price 2–20% below pivot | clears 5-session high | trigger / 8-session low, capped 6% |
| ma20_bounce | Trend Template passes | 20MA rising (vs −5), respected ≥30/40 closes, light-volume tag ≤4 sessions ago (low ≤ MA×1.005, vol < prior-50d avg; heavy-volume tag disqualifies), today reclaim: up day, close > MA, close in top half of range | close / pullback low (≤6 bars), risk ≤ 8% |
| ma50_bounce | Trend Template passes | same shape: window 50, rising vs −10, tag ≤5 sessions, stop span 7 | risk ≤ 10% |
| episodic_pivot | NOT trend-gated (neglect!) | gap: open > prev high OR ≥ +4%; close ≥ +6% vs prev close; vol ≥ 3× prior-50d; close ≥ open; neglect: prior close ≤ 1.10× close 63d ago; day range risk ≤ 12% | gap-day high / gap-day low. AI must verify catalyst; none found ⇒ avoid |
| momentum_burst (watch flag only) | quiet base (5d pre-move drift ≤3%) | +4% day, vol ≥1.5× and > yesterday, close ≤ 20MA×1.15 | not an entry; suppressed on EP days |
| pocket_pivot (flag) | above 50MA, at 10MA (low ≤ MA10×1.02) | up close in top third of range on vol > any down-day vol of last 10 | chart marker |
| anticipation (score 0–100) | ≥2 contractions, price 0–6% below pivot | 30·proximity + 30·final-contraction tightness(≤12%) + 20·dry-up + 20·coiling | feeds "Maturing setups" strip |

Supporting: base_count (uptrend origin = last close < 200MA within ~2y; each
15+ session pause without a new high = one base; 3rd+ = late-stage warning);
tightening_now (last 10d range < 0.6× prior 10d, near 40d highs); buyable
gap-up flag; extension flags (>5% past pivot or >25% above 50MA = extended);
setup warnings (below-50MA, failed breakout, distribution ≥ accumulation+3,
climax +25%/2w); quality score 0–100 (depth 25, final tightness 25, volume
30, pivot-to-high 20); suggested stop = max(10d swing low, entry−8%),
clamped below entry.

### 4.5 Market regime + exposure ladder (per market)

Index health (SPY+QQQ / KLCI): above 50MA & 200MA scored → green/yellow/red;
distribution days (down ≥0.2% on higher vol, expiring on +5% rallies; ≥6 =
downgrade); follow-through day (post −6% correction, +1.5% on rising vol,
day 4+; upgrades red→yellow); breadth (% above 200MA/50MA, new highs/lows —
computable from the warehouse in v3, full universe). Exposure ladder:
green = full 1% risk all buckets; yellow = 0.5%, no forming entries;
red = no new entries, exits only. Rendered in sidebar with per-index MA
chips, dist-day counts, FTD badge, breadth bar.

### 4.6 Plan actionability gate (v2 lesson — keep!)

The trade-plan section renders numbers ONLY if pivot exists AND (bucket ==
swing OR (VCP valid AND price ≤ pivot×1.02)). Otherwise an explicit
"No actionable entry" line stating why (price already cleared stale swing
high / base still forming). Chart-pattern triggers already cleared by price
are labeled "already cleared — confirmation, not an entry."

---

## 5. Fundamentals (the E in SEPA) — code computes, AI reads
### SOURCE (DECIDED 2026-07-22): i3investor, replacing Apify AND yfinance

One module (`scanner/i3_client.py`, shared with the street skill §7.1)
fetches i3investor pages and parses their HTML tables DETERMINISTICALLY
(requests + pandas.read_html — probe 2026-07-18 confirmed server-rendered
HTML, no JS wall). Gemini NEVER parses fundamentals — parsed numbers feed
the mechanical grade; Gemini only interprets them (core value #2).

- From `financial-quarter/{code}` (8+ quarters) + `financial-annual`:
  YoY revenue/NP/EPS growth (None on negative/zero base — never fake a %),
  prior-quarter growth, acceleration flag, margin trend. From `overview`:
  ROE, PE, NTA, DY where present. Mechanical **A–E grade**: EPS ≥25%,
  revenue ≥20%, accelerating, margin expanding, ROE ≥17% — graded only on
  boxes with data; mostly-empty ⇒ grade None (don't punish missing data).
- Cache: `bursa_fundamentals(ticker, data, fetched_at)` (keep the v2 table
  shape); refresh a counter when cache >7 days old AND it's on the board,
  or when the announcements page shows a new QR filing. Nightly fetch
  volume stays polite: board candidates only (~30–45), cache-first,
  throttled sequential.
- Failure honesty: parse failure ⇒ log + grade None + "fundamentals
  unavailable tonight" on the stock page; never guess, never serve >60-day
  stale as current (banner the age).
- Earnings-risk warning (was yfinance's job): derive from i3investor —
  last QR date + Bursa's quarterly reporting deadlines ⇒ expected next-QR
  window; announcements page confirms actual filing. "QR window open" is
  the new warning flag.
- Display: screener NI-YoY column (▲ when accelerating), stock-page panel,
  grade chip; feeds AI payloads.
- (US later: when US reactivates, its fundamentals source is a fresh
  decision — yfinance is not automatically re-admitted.)

---

## 6. Receipts (performance.py — port verbatim, it embodies value #3)

- Signals per candidate row: breakout + early_entry (swing/watchlist buckets
  only), ma20_bounce/ma50_bounce (TT-gated so any bucket that has them),
  episodic_pivot (ANY bucket incl. forming). Target = trigger + 2×risk.
- Grading: trigger = High ≥ trigger within 15 sessions else never_triggered
  (stays on record); then stop-first bar walk 60 sessions: Low ≤ stop ⇒ loss
  −1R; High ≥ target ⇒ win (R on actual risk); else open with current R.
  Same-bar stop+target = loss (conservative).
- Dedupe: one signal per (ticker, type, ~trigger) from earliest sighting;
  seed from existing DB rows so history survives forever; UPSERT on
  (signal_date, ticker, signal_type); **never DELETE**.
- **v3 migration task:** one-time import of v2's `signal_outcomes` (and
  optionally `backtests`, `positions`) from the old Supabase so the track
  record carries over. Map old tickers 1:1 (formats already match).
- UI: per-market sections (headline stats, cumulative-R curve, per-tactic
  tiles with expectancy/win%/PF/n; hide empty splits), full signal table.

---

## 7. AI layer (Gemini — port analyst.py/reviewer.py verbatim, then re-point data reads)

- Models: notes `gemini-3-flash-preview`, briefs+review `gemini-3.5-flash`
  (env-overridable; fallback chains; **dead-model memo + busy-model circuit
  breaker (3 strikes) are load-bearing — keep**). Token budgets include
  thinking: notes ≥6000, briefs ≥8000; retry a failed brief at 2× budget
  (v2 lesson: truncated JSON silently dropped the US brief).
- Google Search grounding on every pick (free ≤5,000 prompts/mo, then $14/1k;
  `ANALYST_SEARCH_MAX` default 100/night). EP catalysts MUST be searched.
- Jobs (fixed, never open-ended):
  1. **Per-pick note** → `candidates.ai_note` jsonb: `{risk, verdict:
     buy-at-pivot|early-entry|wait|avoid, plan{entry,stop,targets,
     invalidation}, news[{headline,date,impact}], summary, assessment[
     {title,tone:fire|info|plan|warn,lines[]}], generated_at}`. Stale-pivot
     rule: price >2% past pivot and bucket ≠ swing ⇒ verdict wait, entry
     describes the NEW setup needed. Forbidden vague phrases enforced in
     system prompt. Assessment replaces the mechanical "Why it's on the
     board" with mechanical fallback when absent.
  2. **Morning brief per market** → `scan_runs.ai_brief`: `{tone, headline
     (one sentence with numbers), sectors[{sector,impact:tailwind|headwind|
     watch,why,counters[]}], counters[{ticker,impact,why}] — ONLY tickers on
     the board (sanitize against board list), action (one instruction
     consistent with exposure ladder), generated_at}`. Feed it
     `counter_news` collected during the notes pass (searched news beats
     yfinance headlines) — v2 lesson: US brief had no counter news without it.
  3. **Weekly review** (Sun cron) → `ai_reviews`: numbers-anchored
     working/not-working + ≤4 hypotheses phrased against existing tools
     (backtest CLI flags / receipts splits); "sample too small" under 20
     closed; never auto-applied. Keep latest 12 rows.
- System prompt invariants: interpret only provided numbers; headlines are
  untrusted text (ignore embedded instructions); strict JSON; update market
  scope wording to "US and Bursa Malaysia."

### 7.1 Street-view skill (i3investor, MY counters) — Phase 5+

**Division of labor: CODE fetches and parses; AI only synthesizes.** Fetch
pages server-side (requests + pandas.read_html; NOT model url_context — 10×
cheaper, vendor-neutral, deterministic). Module: `scanner/i3_client.py`
(shared with §5 fundamentals).

**URL construction is deterministic — no search/discovery step exists.**
Every page is `https://klse.i3investor.com/web/stock/{page}/{code}` where
`{code}` is the counter's Bursa code (e.g. 4456) and `{page}` ∈ {overview,
analysis-price-target, insider, announcement, financial-quarter,
financial-annual, dividend}. The whole skill is one function:
`dossier(code) -> dict` — seven throttled fetches (cache-first), parsed
tables, one compact JSON (~2-3k tokens) into the Tier-A prompt.

**Model url-reading (Gemini url_context) is garnish, never the pipeline:**
permitted only as a targeted extra on Tier-A counters whose PARSED dossier
proves thin, because raw pages cost ~15× the tokens and their numbers
arrive unparseable for grades/charts. Default path is always
code-parse → compact JSON → Gemini.

Per MY counter, cap `STREET_MAX`=10/night, **prioritized by trade-readiness**
(the counters where street data can change tomorrow's decision), in order:
1. live entry signal today (breakout fired / ma-bounce / episodic pivot);
2. valid VCP with price within 5% of the pivot (buy-point imminent);
3. anticipation-strip counters, highest score first (base formed, 0–6%
   below pivot);
4. new-on-board;
5. remaining swing bucket, then nothing else.
Counters with fresh cache don't consume slots — the nightly budget is spent
only on fetching what's new or stale. Scrape-probe finding (2026-07-18):
both page types return server-rendered HTML (200, ~260KB, data present, no
JS wall) — plain requests + table parsing suffices.

Parse from klse.i3investor.com/web/stock/*/{code}:
1. `analysis-price-target`: consensus TP, target count, last 3 actions
   (date, broker, rating, TP). Cache 7 days.
2. `insider`: last-90d director buys/sells (count, value), substantial-
   shareholder (EPF/fund) filings. Cache 1 day.
3. `announcement`: last ~20 announcements (date, title, category). Code
   keyword-classifies each: QR/results, contract win, UMA query, private
   placement / rights issue (DILUTION), bonus/split, ESOS, related-party,
   director/shareholder dealing, others. Cache 1 day. This page is the
   highest-value read on Bursa: catalyst confirmation (contract win or QR
   inside the base), hazard flags (placement = dilution incoming; UMA =
   operator smell feeding the goreng filter), and corporate-action timing.
4. `financial-quarter`: last 8 quarters revenue / net profit / EPS
   (THE fundamentals source, see §5). Cache 7 days.
5. `financial-annual` (or 5y summary): 5-year revenue / net profit / EPS
   trend — is the quarterly acceleration a blip or an inflection on a
   multi-year base? Cache 30 days.
6. key stats from `overview`: PE, NTA, ROE, DY, market cap — valuation
   CONTEXT only (e.g. "PE 11 with EPS +38% YoY" vs "PE 60 priced for
   perfection"); never a filter or score input. Cache 7 days.
7. `dividend`: upcoming ex-date + yield (ex-date near breakout = gap
   hazard warning, same class as earnings risk). Cache 7 days.

Parsed rows → `street_cache(ticker, page, data jsonb, fetched_at)`. The
note payload gains a `street` data block; the note schema gains
`street: {consensus_tp, n_targets, vs_pivot_pct, latest_action,
insider_90d, announcements_flags[], valuation_context, note}`.
AI synthesis rules — relate street data to the computed setup:
- TP headroom vs pivot/2R; a broker upgrade dated inside the base = catalyst.
- Director/EPF buys during the base = the accumulation the chart implies,
  confirmed on paper (the sponsorship leg moomoo used to provide).
- Announcements: contract win / QR beat inside the base = fundamental
  catalyst under the technical setup; placement/rights = dilution warning
  regardless of chart; UMA query = high risk, feeds verdict toward avoid.
- Quarterly vs 5-year: EPS inflection timing vs base formation ("earnings
  turned 2 quarters before the base completed" is the classic SEPA tell).
- Valuation context one line max; ex-date hazards near the entry window.
**No coverage is signal, not failure** — "no street coverage /
institutionally undiscovered" (bullish context for early bases).
Parse failure ⇒ log + "street data unavailable", never guess.
**Street data never enters** fundamentals, grades, buckets, signals, or
receipts — commentary only, badged in the UI. Respect the site: throttled
sequential fetches, ~10 pages/night, cache-first. US analog (optional,
later): stockanalysis.com-style pages via the same module.

---

## 8. Web dashboard (port, then restore Bursa UI)

Pages: `/` screener (regime sidebar w/ exposure ladder, AI morning brief
infographic — responsive grid stacking <900px, maturing-setups strip,
filters incl. market chips ALL/US/MY that survive navigation, table with
tactic tags 20MA/50MA/EP/4%/Ext, NI-YoY column), `/stock/[ticker]` (candles
w/ m20/50/150/200 + RS line + BB + backtest-calibrated R levels + base spans/
VCP zigzag/tactic markers, Trend Template checklist with values, AI trade
plan panel, Why-on-board (AI w/ mechanical fallback), fundamentals panel,
position calculator with **Bursa 100-share board lots**, STALE banner when
the ticker is off the current board), `/performance` receipts per market +
weekly review, `/backtest` run-picker dropdown + widgets + "how trades
ended" + trade table, `/login`. Auth: password cookie + middleware (v2
style acceptable for single user; httpOnly, signed value = nice-to-have).
DB reads resilient to missing new columns (migrations land with scanner,
web deploys first — try/fallback pattern from v2 db.js).

---

### 8.1 Design language (DECIDED after 3 mockup iterations): DARK BRUTALIST

Approved mockup: claude.ai/code/artifact/8b11e8bd-e610-4503-82e3-8134370324ff
(version "dark-brutalist"; the light variants were rejected — full-loud
neubrutalism caused eye strain and focus loss on a data-dense screener;
calm-light lost the personality). Single dark theme, deliberate.

Tokens:
- Ground: canvas `#121210` charcoal; cards `#1B1B18`; hairlines `#33322D`.
- Ink: `#F5F3EA` cream; secondary `#9B988C`.
- Structure: `border-radius: 0` everywhere; borders 2.5px solid CREAM on
  panels (glowing outlines, not cage bars); hairlines inside tables.
- Hard shadows (zero blur), COLORED — the dark-brutalist signature:
  pink `8px 8px 0 0 #FF90E8` on regime heroes; cream 4px on panels;
  yellow 3-4px on the maturing strip; never on table rows.
- Accents: brand pink `#FF90E8` (brand block, EP tag, focus rings, key
  shadows), yellow `#FFD23F` (maturing strip, MY market marks), blue
  `#74B9FF` (info). Flat fills only, no gradients.
- Semantics (brightened for dark ground, sacred meaning): up `#2EE6A8`,
  down `#FF6B52`; regime blocks = the ONLY full-color slabs (bright
  green/yellow/red fill, black text, cream border, pink hero shadow,
  exposure rule in 900-weight caps). Never color-only state — pair with
  labels/icons.
- Loudness budget (the lesson from mockup v1): regime blocks + one CTA
  are the only shouting elements; EP is the only filled tag; all other
  tags are outline-only in semantic colors; the table is the calm
  protagonist (flat rows, hairline rules, hover = subtle tint).
- Type: Space Grotesk 700 (headings/display), Inter 400 (body),
  Space Mono (all numbers, tabular-nums) via next/font.
- Interaction: hover translate(-2px,-2px) + shadow grows; press
  translate(3px,3px) + shadow removed; visible pink focus outlines;
  respect prefers-reduced-motion.
- Charts: card ground, cream axes/frame 2.5px, MAs in accents, candles
  in up/down colors — style lives in the frame, not the data.
- Mobile: shadows drop one tier, borders stay. Light mode: none (single
  dark theme is the decision, matching night-and-morning usage).

Engine (keep): per-market separate runs (own equity/stats/row), strategies
breakout | ma20_bounce | ma50_bounce | episodic_pivot (vectorized mirrors of
live rules; per-market calendars never mixed), signals day t → fill t+1 open
with per-side slippage+fees (US 0.10%+0.05%, MY 0.30%+0.18%, CLI-overridable),
same-day stop check on entry bar, stop/50MA-break/max-hold exits, 1% risk
sizing on current equity, 25% position cap, max 8 open, end-of-data
liquidation tagged, NaN-safe jsonb stats, nightly-auto rows pruned (keep 30).

**v3 upgrade A — full-universe mode (`--universe US|MY`):** test against the
whole exchange (incl. delisted symbols from EODHD directory) with the
liquidity filter applied per-day inside the simulation and RS ranked
cross-sectionally per day across the full pool. This kills the
board-selection hindsight bias and answers utilization (v2's 4% CAGR was
mostly idle capital). Fetch via `history()` on demand; cache to parquet in
the Actions workspace; chunk if memory demands.

**v3 upgrade B — bootstrap risk panel:** resample the trade list 10,000×
(numpy, no new deps): CAGR distribution (5/50/95 pct), max-drawdown
distribution, P(DD > 25%), risk-of-ruin proxy at 1%/2% risk. Render on
`/backtest`. Plus quantstats monthly-returns heatmap + drawdown table.

**v3 upgrade C — factor validation (weekly):** decile forward-return table
(20d) for quality score, RS rank, A–E grade, anticipation score across all
candidates history; code computes, weekly-review AI reads. If a score's
deciles don't slope, say so on /performance.

---

## 10. Database schema (Postgres/Supabase)

Port v2 `schema.sql` + migrations 001–016, then add:
`candles`, `candles_meta` (§3.3). Existing tables kept as-is: scan_runs
(regime jsonb, ai_brief, sector_news), candidates (~30 cols incl. checks/
vcp/setup/patterns/levels/candles/fundamentals/ai_note jsonb), sector_ranks,
signal_outcomes (UNIQUE signal_date+ticker+signal_type), backtests,
positions + position_signals (journal, UI still TODO), watchlist, settings,
ticker_meta, bursa_fundamentals, ai_reviews. Migrations auto-applied by the
scanner on every run (IF NOT EXISTS style; failures abort loudly).

---

## 11. Workflows & scheduling (GitHub Actions)

| Workflow | Trigger | Notes |
|---|---|---|
| scan-my.yml | cron `30 12 * * 1-5` + dispatch(force) | Bursa closes 09:00 UTC; EODHD documented window close +2–3h — live probe 2026-07-22 showed fresh at 11:40 UTC; cron 12:30 has margin, board ready 8:30pm MYT same evening. THE only nightly scan in v3.0 (US parked) |
| ai-analyst.yml | workflow_run on scan-my (success) + dispatch | 60-min timeout; MY-only load ~30-45 notes |
| backtest.yml | workflow_run on scan-my success + dispatch(tickers/strategy/years/risk/label) | MY-only; auto rows labeled `nightly YYYY-MM-DD`, pruned to 30 |
| weekly-review.yml | cron `0 1 * * 0` + dispatch | |
| (parked) scan-us.yml | — | reactivation = add this cron `0 22 * * 1-5` + US market config; nothing else |
| (removed) bursa-fundamentals.yml | — | Apify dropped; fundamentals fetched by scan-my via i3_client (§5) |

**11.1 Secrets:** `DATABASE_URL` (Supabase session pooler, port 5432),
`EODHD_API_TOKEN`, `GEMINI_API_KEY`; Vercel env:
`DATABASE_URL`, `DASHBOARD_PASSWORD`. Secrets never in code or logs; inputs
passed to shell via env vars, never interpolated (injection).
**11.2 Tests run first** in every scan workflow (pytest gate before scanning).
**11.3 Freshness guard:** every chained/cron job checks the latest
`scan_runs.run_date` for its market and exits 0 with a log line if it isn't
the expected session — never analyze/backtest stale data silently.

---

## 12. Build phases with acceptance criteria

**Phase 0 — Validation (before writing code; needs the paid month)**
Buy EODHD All-World. Verify: (a) `1155.KLSE` and a small-cap (e.g. `0138`)
return ≥300 daily bars; (b) **freshness — the v1 killer**: on ≥3 live trading days, measure WHEN
the KLSE bulk EOD for TODAY's session (bar date == today) actually becomes
available (EODHD documents close +2–3h ⇒ expect ~11:00–12:00 UTC; probe
hourly 10:00–13:00) and confirm US by 21:45 UTC (documented: close +15min
for NYSE/NASDAQ). Yahoo/yfinance routinely served Bursa bars a full day
late (scan on the 14th still showing the 13th) — a silently day-late board
destroys momentum entries. Not "data exists": THE LATEST BAR IS TODAY'S.
Set the scan-my cron from the measured time + 30min margin; (c) adjusted prices sane across a
recent split (pick any known splitter); (d) delisted symbols present in the
US directory; (e) index codes for SPY-equivalent + KLCI resolved;
(f) **KLSE coverage census** — pull the full KLSE symbol directory, compare
count vs Bursa's actual listed companies (~950–1,000 incl. ACE), and spot-
check history presence for 30 random counters weighted toward small/ACE
names PLUS every specific counter the owner remembers yfinance failing on
(owner to supply list). Yahoo's known Bursa gaps are a v1 pain point; this
test is the reason we're paying. Pre-purchase probe 2026-07: ACE small-cap
0250.KLSE shows live data on EODHD's public page — promising, not proof.
**Exit criteria:** (a)–(e) pass and census ≥95% coverage with gaps
explainable (suspended/LEAP-market), else stop and reassess (iTick next).

**RESULTS (live probe, 2026-07-22, subscribed key):**
- (a) DEPTH ✓ — 1155.KLSE: 631 bars since 2024-01-01, no gaps, full OHLCV
  + adjusted close.
- (b) FRESHNESS ✓ day 1 of 3 — KLSE bulk returned 937 symbols ALL dated
  2026-07-22 at 11:40 UTC (2h40m after close; inside documented SLA;
  12:30 UTC cron has margin). US correctly showed prior session pre-close.
  NOTE: bulk contains only counters that TRADED that day (~937 of 1,073);
  thin ACE counters legitimately skip days — absence ≠ staleness. The
  freshness gate keys on the bulk's modal date / liquid benchmarks.
- (c) SPLITS endpoint ✓ — AAPL 7:1 2014-06-09 and 4:1 2020-08-31 correct;
  full adjusted-vs-raw stitch check moves to Phase 1.
- (d) DELISTED ✓ — US directory: 51,546 live symbols (18,194 common),
  **58,735 delisted** — the survivorship fix is real and large.
- (e) INDEX CODES — GSPC.INDX ✓. No raw KLCI index in catalog; DECISION:
  MY regime uses 0820EA.KLSE (KLCI ETF — price fresh ✓, 637 bars) for MA
  health/FTD price, but its volume is unusable (~700 units/day) — so MY
  distribution days & volume signals use AGGREGATE exchange volume
  (sum of all KLSE turnover per day from the warehouse), a truer
  institutional footprint than any single instrument.
- (f) CENSUS ✓ preliminary — KLSE directory 1,082 symbols (1,073 common)
  vs ~1,000 Bursa-listed: full coverage, surplus = suspended/inactive.
REMAINING: freshness days 2–3 (rerun the probe after the next closes),
US bulk timing at 21:45 UTC, split-stitch check during Phase 1 backfill.

**Ongoing coverage guarantee (Phase 2+):** the nightly bulk ingest logs
`symbols received vs directory expected` per exchange; a drop >2% turns the
scan log loud. Missing counters must be VISIBLE, never silently skipped —
the v1 behavior ("invalid codes are skipped automatically") is banned.

**Phase 1 — Skeleton + data spine (the only new engineering)**
New repo; port scanner modules + tests verbatim (they must pass unmodified
except import paths); `eodhd_client.py` + `warehouse.py` + backfill script;
candles DDL. **Accept:** backfill completes within quota; warehouse row
counts match symbol counts × window; `history("AAPL")` shape identical to
v2's download_batch output (tests prove indicators run unchanged).

**Phase 2 — Scans live**
scan.py re-pointed at the warehouse; both market configs (US + MY restored:
currency RM, lots 100, calendar XKLS, its own regime index); two cron
workflows; full-universe RS. **Accept:** two consecutive nightly runs green
for both markets; candidates in DB with all tactic fields; regime + breadth
computed from warehouse; RS pool size ≈ full universe (thousands, not 280).

**Phase 3 — Web live**
Vercel deploy of the ported app with Bursa UI restored (market chips, RM
formatting, lots). **Accept:** all five pages render both markets; no 500 on
missing-column gap; mobile brief stacks; stale banner works.

**Phase 4 — Chains + AI**
ai-analyst re-pointed and chained to both scans; briefs per market; notes
with search; weekly review; receipts import from v2 Supabase (one-time
script, verify counts). **Accept:** morning brief shows BOTH markets with
counters-in-news; every EP note cites a searched catalyst or says none
found; receipts page shows imported history + new signals accruing.

**Phase 5 — Backtest upgrades**
Full-universe mode + bootstrap panel + monthly heatmap + factor-validation
job. **Accept:** `--universe US --years 3` completes in Actions within
runtime limits (chunk if needed); bootstrap panel renders percentiles;
decile table lands in weekly review.

**Phase 6 — Hardening + docs**
README truthful end-to-end; .env.example current; kill v2 leftovers list
(old workflows off, VPS crontab disabled once v3 is proven ~1 week parallel).

**Sequencing note:** run v2 and v3 in parallel ≥5 trading days comparing
boards before switching off v2 — same-day candidate overlap should be high
(≥70% of swing bucket); investigate every large divergence (usually data
quality, occasionally a port bug).

---

## 13. Cost & quota budget (steady state)

Nightly API calls: 2 bulk EOD (≈200 call-units) + ~90 candidate enrichment
histories only when charts need >window (rare) + earnings lookups (yfinance,
free) → trivially inside 100k/day. Gemini: ~90 notes × ~3k tokens in / 1k out
on flash, tiered per §13.1 ≈ **RM45 expected / RM50 cap**; search free tier
covers usage ~10× over at defaults. Supabase: monitor `candles`
size monthly (§3.3). **Total all-in ≈ RM145–150/mo expected, RM150 hard cap
(≈ RM125–135 after the EODHD annual switch).**

### 13.1 AI budget: RM68/month cap (inside the RM150 all-in), tiered by conviction (DECIDED)

Research depth follows the SAME mechanical priority queue as the street
skill (§7.1) — one deterministic rule decides scraping, model tier, and
search; the AI never chooses who deserves AI.

| Tier | Mechanical trigger | Treatment | Est/mo |
|---|---|---|---|
| A — full dossier (~8–12/night) | live entry signal today, OR valid VCP ≤5% below pivot, OR top anticipation scores, OR new-on-board; must be Template-passing and not extended (EPs exempt from the trend gate) | **pro-class model from day one** (RM82 EODHD price freed the headroom), street pages, searched catalyst, full plan + assessment | ~RM38 |
| B — standard note (rest of swing/watchlist) | on board, not yet actionable | cheap flash model, cached news, no street | ~RM10 |
| C — mechanical only (position/forming) | radar names | template text, zero AI spend | RM0 |
| Briefs ×2 + weekly review (pro-class — 4 calls/mo costs pennies) + Sunday deep-dive | — | — | ~RM8 |
| Retry/degraded buffer | — | — | ~RM7 |

Expected burn ≈ RM60–65; cap RM68. Search stays inside the free 5,000
grounded prompts/month (expected usage ~400). Hot markets produce more
Tier-A counters (cap absorbs it — quality degrades to flash before the
cap breaches, never a surprise bill); red-regime months burn far less.
Guardrails: `ANALYST_MAX_NOTES`, `STREET_MAX`, per-tier model env vars,
nightly usage log line — glance weekly for the first month.
**The unspent headroom is deliberate:** it waits for receipts evidence
before being allocated, and its designated future home is the portfolio
coach (AI review of actual journal trades vs the rules) once the
positions-journal UI exists. Never spend it on: pro models for the long
tail, extra searches on quiet counters, chart-vision, or anything that
makes AI output resemble a signal.

## 14. Risks & mitigations

| Risk | Mitigation |
|---|---|
| EODHD MY data quality (gaps/late bulk) | Phase 0 validation; hard-fail on missing bulk; parallel-run week |
| (removed) yfinance | no longer in the stack |
| i3investor page structure changes | parse failure = loud log + grade None + page banner, never guess; parsers are table-shape-tolerant; fix at leisure |
| Supabase free tier outgrown | shrink window to 320d, or prune sub-liquidity tickers from warehouse |
| Gemini model retirements | fallback chains + env overrides (already built) |
| Loss of moomoo institutional/whale data | accepted feature loss; revisit via 13F (US) later |
| Actions runtime limits on full-universe backtests | parquet caching + chunked universes + `MAX_*` env caps |
| Receipts continuity broken in migration | import script verifies row counts + spot-checks 10 known signals |

## 15. Decisions

1. **DECIDED: everything fresh.** New GitHub repo, new Supabase project, new
   Vercel project. Consequence: the one-time receipts import (§6, Phase 4)
   is MANDATORY — it needs read-only access to the old Supabase
   `DATABASE_URL` exactly once; verify imported row counts before switching
   v2 off. v2 stays untouched as reference + fallback during the parallel
   week.
2. New repo name — owner to choose (create empty on GitHub first).
3. Keep or retire the VPS after the parallel week (only reason to keep:
   OpenD whale-flow, not ported in v3.0).
4. EODHD monthly vs annual billing — start monthly for the validation month,
   switch to annual (discounted) after Phase 2 passes.

### 15.1 Fresh-infrastructure checklist (owner clicks, in order)

1. GitHub: create empty private repo.
2. Supabase: new project, **Singapore region**, grab the **Session pooler**
   connection string (port 5432, NOT 6543). No SQL to run — the scanner
   creates every table itself on first run.
3. EODHD: All-World plan (monthly), copy API token.
4. Google AI Studio: Gemini API key (can reuse the existing one — key reuse
   is fine, it's account-level).
6. GitHub repo → Settings → Secrets → Actions: `DATABASE_URL`,
   `EODHD_API_TOKEN`, `GEMINI_API_KEY`, `APIFY_TOKEN`.
7. Vercel: new project importing the repo, **Root Directory = `web`**, env
   vars `DATABASE_URL` + `DASHBOARD_PASSWORD` (pick a NEW password), deploy.
8. For Phase 4 receipts import only: provide the OLD Supabase
   `DATABASE_URL` as a temporary secret `V2_DATABASE_URL`, delete it after
   the import verifies.

## 16. Glossary (signal_type values in receipts)

`breakout` pivot breakout · `early_entry` cheat entry inside base ·
`ma20_bounce` / `ma50_bounce` pullback-bounce at rising MA ·
`episodic_pivot` gap-on-volume from neglect. Buckets: `swing` (buy-point
ready), `position` (trend intact, no base), `watchlist` (base forming /
extended), `forming` (near-miss template, early radar).

---
*End of plan. When executing with an AI session: give it this file plus read
access to the v2 repo (github.com/firdisml/sepa-board) and the four secrets.
Execute phases in order; do not skip Phase 0.*
