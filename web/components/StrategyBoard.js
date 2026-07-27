"use client";

/* Which tactic is actually paying, ranked, and whether that is drifting.
   Two things make this more than a summary table:

   1. RECENT vs ALL-TIME. One number over all history hides a tactic that
      stopped working two months ago. The 30-day column is the drift check.
   2. LIVE vs BACKTEST. The backtest says what the rule earned historically;
      receipts say what it is earning now, on signals actually published. A
      large gap either way is the thing worth investigating. */

// Measured on the FAIR basis: 5 years, full Bursa universe, delisted counters
// included, RS ranked across all ~1,062 names (deep-history runs 2026-07-27).
// These REPLACE the earlier 2y figures, which came from a survivorship-biased
// ~13-month window that sat entirely inside one green regime. Every ranking
// below moved; ma20_bounce fell from an apparent +0.53R to +0.20R and lost
// first place to breakout.
const BACKTEST = {
  breakout: 0.33, buyable_gap_up: 0.30, ma20_bounce: 0.20,
  pocket_pivot: 0.08, ma50_bounce: -0.01, episodic_pivot: -0.35,
};

const LABEL = {
  breakout: "Breakout", early_entry: "Early entry", ma20_bounce: "20MA bounce",
  ma50_bounce: "50MA bounce", episodic_pivot: "Episodic pivot",
  pocket_pivot: "Pocket pivot", buyable_gap_up: "Buyable gap-up",
};

function stats(rows) {
  const closed = rows.filter((r) => r.outcome === "win" || r.outcome === "loss");
  const rs = closed.map((r) => Number(r.r_multiple)).filter((v) => Number.isFinite(v));
  const wins = rs.filter((v) => v > 0);
  const losses = rs.filter((v) => v <= 0);
  const grossW = wins.reduce((a, b) => a + b, 0);
  const grossL = Math.abs(losses.reduce((a, b) => a + b, 0));
  const triggered = rows.filter((r) => r.triggered).length;
  return {
    signals: rows.length,
    closed: rs.length,
    open: rows.filter((r) => r.outcome === "open").length,
    expectancy: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
    winRate: rs.length ? (wins.length / rs.length) * 100 : null,
    pf: grossL > 0 ? grossW / grossL : null,
    triggerRate: rows.length ? (triggered / rows.length) * 100 : null,
    totalR: rs.reduce((a, b) => a + b, 0),
  };
}

const fmtR = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}R`);

export default function StrategyBoard({ rows }) {
  if (!rows?.length) return null;

  const cutoff = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const types = [...new Set(rows.map((r) => r.signal_type))];

  const table = types.map((t) => {
    const all = rows.filter((r) => r.signal_type === t);
    const recent = all.filter((r) => (r.signal_date || "").slice(0, 10) >= cutoff);
    return { type: t, all: stats(all), recent: stats(recent), bt: BACKTEST[t] };
  }).sort((a, b) => {
    // rank by expectancy, but a tactic with no closed trades yet sorts last
    // rather than appearing to beat one that has actually been tested
    const av = a.all.expectancy, bv = b.all.expectancy;
    if (av == null && bv == null) return b.all.signals - a.all.signals;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  });

  const totalClosed = table.reduce((s, r) => s + r.all.closed, 0);

  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <h3>Strategy scoreboard — which tactic is paying</h3>
      <div className="reasoning" style={{ marginBottom: 10 }}>
        Ranked by expectancy on <b>closed</b> signals only. Backtest is the
        5-year full-universe result <i>including delisted counters</i>; live is
        what these rules have actually earned on published signals. A gap
        between them is the thing to look at
        {totalClosed < 20 && (
          <> — and with <b>{totalClosed} closed signals</b> so far, treat every
          row here as provisional. Twenty is the point these numbers start
          meaning something.</>
        )}
      </div>
      <div className="bt-wrap">
        <table className="bt sb">
          <thead>
            <tr>
              <th className="ta-l">Tactic</th>
              <th className="ta-r">Signals</th>
              <th className="ta-r">Closed</th>
              <th className="ta-r">Open</th>
              <th className="ta-r">Win%</th>
              <th className="ta-r">Expectancy</th>
              <th className="ta-r">Last 30d</th>
              <th className="ta-r">PF</th>
              <th className="ta-r">Trigger%</th>
              <th className="ta-r">Backtest</th>
              <th className="ta-l">Read</th>
            </tr>
          </thead>
          <tbody>
            {table.map(({ type, all, recent, bt }) => {
              const live = all.expectancy;
              const drift = live != null && bt != null ? live - bt : null;
              let read = "no closed trades yet";
              if (live != null) {
                if (bt != null && bt < 0 && live < 0) read = "negative, as backtested";
                else if (drift != null && drift < -0.3) read = "underperforming its backtest";
                else if (drift != null && drift > 0.3) read = "beating its backtest";
                else read = "in line with backtest";
              }
              return (
                <tr key={type}>
                  <td className="ta-l">
                    <span className="sym">{LABEL[type] || type}</span>
                    {bt != null && bt < 0 && <span className="tag bad">neg edge</span>}
                  </td>
                  <td className="ta-r num">{all.signals}</td>
                  <td className="ta-r num">{all.closed}</td>
                  <td className="ta-r num">{all.open}</td>
                  <td className="ta-r num">{all.winRate == null ? "—" : `${all.winRate.toFixed(0)}%`}</td>
                  <td className={"ta-r num" + (live == null ? "" : live > 0 ? " pos" : " neg")}>
                    {fmtR(live)}
                  </td>
                  <td className={"ta-r num" + (recent.expectancy == null ? "" : recent.expectancy > 0 ? " pos" : " neg")}>
                    {recent.closed ? fmtR(recent.expectancy) : "—"}
                  </td>
                  <td className="ta-r num">{all.pf == null ? "—" : all.pf.toFixed(2)}</td>
                  <td className="ta-r num">{all.triggerRate == null ? "—" : `${all.triggerRate.toFixed(0)}%`}</td>
                  <td className={"ta-r num" + (bt == null ? "" : bt > 0 ? " pos" : " neg")}>
                    {bt == null ? "—" : fmtR(bt)}
                  </td>
                  <td className="ta-l dim" style={{ fontSize: 11 }}>{read}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="legend">
        <span className="right">
          Trigger% is how often a published signal ever reached its entry —
          never-triggered signals stay on the record rather than being dropped.
        </span>
      </div>
    </div>
  );
}
