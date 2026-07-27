/* Which tactic's most recent backtest evidence is best, at a glance — above
   the fold on /backtest, before the reader picks through individual runs.
   One row per strategy (its newest saved run), ranked by expectancy. */

const LABEL = {
  breakout: "Breakout", early_entry: "Early entry", ma20_bounce: "20MA bounce",
  ma50_bounce: "50MA bounce", episodic_pivot: "Episodic pivot",
  pocket_pivot: "Pocket pivot", buyable_gap_up: "Buyable gap-up",
  ma200_reclaim: "200MA reclaim", undercut_rally: "Undercut & rally",
};

// Tactics the board can actually SELECT as a counter's active_tactic. Every
// other row here is evidence — measured, recorded, and deliberately not
// traded. Without this distinction a rejected tactic sitting in the winner
// table reads as something the board might act on.
const LIVE = new Set(["breakout", "ma20_bounce", "ma50_bounce"]);
const REJECTED_NOTE = {
  episodic_pivot: "hazard — never a buy plan",
  ma200_reclaim: "reversal, measured negative",
  undercut_rally: "reversal, measured negative",
  pocket_pivot: "measured weak, not promoted",
  buyable_gap_up: "measured well, awaiting a second window",
};

const fmtR = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}R`);
const fmtPct = (v) => (v == null ? "—" : `${v}%`);

export default function BacktestWinner({ runs }) {
  if (!runs?.length) return null;

  // newest row per strategy: `runs` is already created_at DESC, so the
  // first occurrence of a strategy key IS its newest saved backtest
  const newestByStrategy = new Map();
  for (const r of runs) {
    const strat = r.params?.strategy || "breakout";
    if (!newestByStrategy.has(strat)) newestByStrategy.set(strat, r);
  }

  const table = [...newestByStrategy.values()].sort((a, b) => {
    const av = a.stats?.expectancy_r, bv = b.stats?.expectancy_r;
    // a strategy with no expectancy yet (degenerate run) sorts last rather
    // than appearing to beat one that has actually been tested
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  });

  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <h3>Winner — newest result per strategy</h3>
      <div className="reasoning" style={{ marginBottom: 10 }}>
        One row per tactic: its most recently saved backtest, ranked by
        expectancy. Rows marked <b>not traded</b> are evidence, not
        recommendations — measured on the same basis and deliberately kept out
        of the board&apos;s rotation. A tactic can also be stale here if it
        hasn&apos;t been re-run since; check &quot;As of&quot; first.
      </div>
      <div className="bt-wrap">
        <table className="bt win">
          <thead>
            <tr>
              <th className="ta-l">Tactic</th>
              <th className="ta-r">Expectancy</th>
              <th className="ta-r">CAGR</th>
              <th className="ta-r">Max DD</th>
              <th className="ta-r">Win%</th>
              <th className="ta-r">PF</th>
              <th className="ta-r">Trades</th>
              <th className="ta-l">As of</th>
            </tr>
          </thead>
          <tbody>
            {table.map((r, i) => {
              const s = r.stats || {};
              const exp = s.expectancy_r;
              return (
                <tr key={r.id}>
                  <td className="ta-l">
                    <span className="sym">{LABEL[r.params?.strategy] || r.params?.strategy || "—"}</span>
                    {i === 0 && <span className="tag good">WINNER</span>}
                    {exp != null && exp < 0 && <span className="tag bad">neg edge</span>}
                    {!LIVE.has(r.params?.strategy) && (
                      <span className="tag neutral"
                            title={REJECTED_NOTE[r.params?.strategy] || "not in the live rotation"}>
                        not traded
                      </span>
                    )}
                  </td>
                  <td className={"ta-r num" + (exp == null ? "" : exp > 0 ? " pos" : " neg")}>
                    {fmtR(exp)}
                  </td>
                  <td className={"ta-r num" + (s.cagr_pct == null ? "" : s.cagr_pct > 0 ? " pos" : " neg")}>
                    {fmtPct(s.cagr_pct)}
                  </td>
                  <td className="ta-r num neg">{fmtPct(s.max_drawdown_pct)}</td>
                  <td className="ta-r num">{s.win_rate_pct == null ? "—" : `${s.win_rate_pct}%`}</td>
                  <td className="ta-r num">{s.profit_factor ?? "—"}</td>
                  <td className="ta-r num">{s.trades ?? "—"}</td>
                  <td className="ta-l dim" style={{ fontSize: 11 }}>
                    {r.created_at ? String(r.created_at).slice(0, 10) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
