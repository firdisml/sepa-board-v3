import { latestBundle, backtests, backtestById } from "@/lib/db";
import Shell from "@/components/Shell";
import LineChart from "@/components/LineChart";
import RunPicker from "@/components/RunPicker";

export const dynamic = "force-dynamic";

const REASONS = {
  stop: ["Stop hit", "var(--red)"],
  ma50_break: ["Closed under 50-day MA", "var(--amber)"],
  time: ["Time exit (max hold)", "var(--blue)"],
  end_of_data: ["Still open at end of data", "var(--faint)"],
};

export default async function Backtests({ searchParams }) {
  const [bundle, runs] = await Promise.all([latestBundle(), backtests()]);
  const regime = bundle?.run?.regime || null;
  const selId = searchParams?.id ? Number(searchParams.id) : runs[0]?.id;
  const sel = selId ? await backtestById(selId) : null;
  const s = sel?.stats || {};
  const trades = sel?.trades || [];
  const curve = (sel?.equity || []).map((p) => ({ t: p.t, v: p.eq }));

  // exit-reason breakdown — where the trades actually ended, and at what R
  const byReason = {};
  for (const t of trades) {
    const k = t.reason || "other";
    byReason[k] = byReason[k] || { n: 0, r: 0 };
    byReason[k].n += 1;
    byReason[k].r += Number(t.r || 0);
  }
  const reasonRows = Object.entries(byReason).sort((a, b) => b[1].n - a[1].n);
  const winPct = s.win_rate_pct ?? null;

  const tile = (k, v, color) => (
    <div className="wtile" key={k}>
      <div className="k">{k}</div>
      <div className="v" style={color ? { color } : undefined}>{v ?? "—"}</div>
    </div>
  );

  return (
    <Shell regime={regime} asOf={bundle?.run?.run_date?.slice(0, 10)} active="/backtest">
      <div className="page-head">
        <div>
          <h1>Backtests</h1>
          <div className="asof">Bar-by-bar replay of the board's breakout rules — signals use only same-day data, fills at next open, stop-first grading. Refreshes automatically after every successful nightly scan.</div>
        </div>
        {runs.length > 0 && <RunPicker runs={runs} selected={sel?.id} />}
      </div>

      {!sel ? (
        <div className="panel"><h3>No backtests stored yet</h3>
          <div className="reasoning">GitHub → Actions → <b>backtest</b> → Run workflow, or wait for tonight's scan — the backtest chains automatically. Results land here, one run per market.</div>
        </div>
      ) : (
        <>
          <div className="stats">
            <div className="stat"><div className="k">Expectancy per trade</div><div className={"v " + ((s.expectancy_r ?? 0) > 0 ? "green" : "red")}>{s.expectancy_r ?? "—"}R</div><div className="sub">{s.trades} trades over {sel.params?.years}y · {(sel.params?.tickers || []).length} tickers</div></div>
            <div className="stat"><div className="k">CAGR</div><div className={"v " + ((s.cagr_pct ?? 0) > 0 ? "green" : "red")}>{s.cagr_pct}%</div><div className="sub">start {Number(sel.params?.start_equity || 0).toLocaleString()} → final {Number(s.final_equity || 0).toLocaleString()}</div></div>
            <div className="stat"><div className="k">Max drawdown</div><div className="v red">{s.max_drawdown_pct}%</div><div className="sub">worst peak-to-trough on the equity curve</div></div>
            <div className="stat"><div className="k">Profit factor</div><div className={"v " + ((s.profit_factor ?? 0) >= 1.5 ? "green" : "amber")}>{s.profit_factor ?? "—"}</div><div className="sub">gross wins ÷ gross losses · ≥1.5 is healthy</div></div>
          </div>

          <div className="detail-grid">
            <div className="panel">
              <h3>
                Equity curve — {sel.label || `run #${sel.id}`}
                {sel.params?.market && <span className="tag us">{sel.params.market} only</span>}
              </h3>
              <LineChart points={curve} height={240} fmt={(v) => v.toLocaleString()} />
              <div className="legend">
                <span className="right">risk {sel.params?.risk_pct}%/trade · stop {Math.round((sel.params?.stop_pct || 0) * 100)}% · max {sel.params?.max_open} open · costs modeled per side</span>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div className="panel">
                <h3>Trade quality</h3>
                {winPct != null && (
                  <>
                    <div className="splitbar" title={`${winPct}% winners`}><span className="w" style={{ width: `${winPct}%` }} /></div>
                    <div className="sub" style={{ marginBottom: 8 }}>{winPct}% winners · {s.trades} trades · avg hold {s.avg_hold_days ?? "—"}d</div>
                  </>
                )}
                <div className="wtiles">
                  {tile("Avg win", s.avg_win_r != null ? `+${s.avg_win_r}R` : null, "var(--green)")}
                  {tile("Avg loss", s.avg_loss_r != null ? `${s.avg_loss_r}R` : null, "var(--red)")}
                  {tile("Sharpe", s.sharpe)}
                  {tile("Sortino", s.sortino)}
                  {tile("Volatility (ann.)", s.volatility_pct != null ? `${s.volatility_pct}%` : null)}
                  {tile("Costs paid", s.total_fees != null ? s.total_fees.toLocaleString() : null, "var(--amber)")}
                </div>
              </div>

              <div className="panel">
                <h3>How trades ended</h3>
                {reasonRows.length === 0 && <div className="reasoning">No trades in this run.</div>}
                {reasonRows.map(([k, v]) => {
                  const [label, color] = REASONS[k] || [k, "var(--dim)"];
                  const avg = v.n ? (v.r / v.n).toFixed(2) : "—";
                  return (
                    <div className="xrow" key={k}>
                      <span><span className="xdot" style={{ background: color }} />{label}</span>
                      <span className="n num">{v.n}×</span>
                      <span className="num" style={{ color: v.r / v.n > 0 ? "var(--green)" : "var(--red)" }}>{avg}R avg</span>
                    </div>
                  );
                })}
                <div className="vcp-note" style={{ marginTop: 10 }}>
                  Net of per-side slippage + fees (US 0.10%+0.05%, MY 0.30%+0.18%). v1 gates on
                  Trend Template + breakout + volume, not VCP quality — the live board's picks are
                  a tighter subset of these trades.
                </div>
              </div>
            </div>
          </div>

          <div className="tbl-wrap" style={{ marginTop: 14 }}>
            <table className="screener">
              <thead><tr><th>Entry</th><th>Exit</th><th>Ticker</th><th>Entry px</th><th>Exit px</th><th>Stop</th><th>R</th><th>Held</th><th>Exit reason</th></tr></thead>
              <tbody>
                {trades.length === 0 && <tr><td colSpan="9" className="empty">No trades in this run.</td></tr>}
                {[...trades].reverse().slice(0, 200).map((t, i) => (
                  <tr key={i} style={{ cursor: "default" }}>
                    <td className="num">{t.entry_date}</td>
                    <td className="num">{t.exit_date}</td>
                    <td className="sym">{t.ticker.replace(".KL", "")}</td>
                    <td className="num">{Number(t.entry).toFixed(2)}</td>
                    <td className="num">{Number(t.exit).toFixed(2)}</td>
                    <td className="num">{Number(t.stop).toFixed(2)}</td>
                    <td className="num" style={{ color: t.r > 0 ? "var(--green)" : "var(--red)" }}>{Number(t.r).toFixed(2)}</td>
                    <td className="num">{t.held}d</td>
                    <td><span className="xdot" style={{ background: (REASONS[t.reason] || [null, "var(--dim)"])[1] }} />{(REASONS[t.reason] || [t.reason])[0]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Shell>
  );
}
