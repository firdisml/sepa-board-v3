import { latestBundle, latestReview, signalOutcomes } from "@/lib/db";
import Shell from "@/components/Shell";
import LineChart from "@/components/LineChart";

export const dynamic = "force-dynamic";

const SIGNAL_TYPES = [
  ["Breakout", "breakout"],
  ["Early entry", "early_entry"],
  ["20MA bounce", "ma20_bounce"],
  ["50MA bounce", "ma50_bounce"],
  ["Episodic pivot", "episodic_pivot"],
];

function agg(rows) {
  const closed = rows.filter((r) => r.outcome === "win" || r.outcome === "loss");
  const wins = closed.filter((r) => r.outcome === "win");
  const losses = closed.filter((r) => r.outcome === "loss");
  const sumW = wins.reduce((s, r) => s + Number(r.r_multiple || 0), 0);
  const sumL = Math.abs(losses.reduce((s, r) => s + Number(r.r_multiple || 0), 0));
  const triggered = rows.filter((r) => r.triggered);
  return {
    signals: rows.length,
    triggerRate: rows.length ? Math.round((triggered.length / rows.length) * 100) : null,
    closed: closed.length,
    winRate: closed.length ? Math.round((wins.length / closed.length) * 100) : null,
    expectancy: closed.length
      ? (closed.reduce((s, r) => s + Number(r.r_multiple || 0), 0) / closed.length).toFixed(2)
      : null,
    pf: sumL > 0 && wins.length ? (sumW / sumL).toFixed(2) : null,
    avgDays: triggered.length
      ? (triggered.reduce((s, r) => s + (r.days_to_trigger ?? 0), 0) / triggered.length).toFixed(1)
      : null,
  };
}

// cumulative R over time (closed signals only) — the consistency picture
function rCurve(rows) {
  let cum = 0;
  const curve = rows
    .filter((r) => r.outcome === "win" || r.outcome === "loss")
    .map((r) => ({ t: r.signal_date, v: (cum += Number(r.r_multiple || 0)) }));
  return { curve, cum };
}

function MarketSection({ label, tagClass, rows }) {
  const s = agg(rows);
  const { curve, cum } = rCurve(rows);
  const typeSplits = SIGNAL_TYPES
    .map(([name, type]) => [name, rows.filter((r) => r.signal_type === type)])
    .filter(([, subset]) => subset.length > 0);
  return (
    <section>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <h1 style={{ fontSize: 17 }}>{label} <span className={`tag ${tagClass}`}>{s.signals} signals</span></h1>
      </div>

      <div className="stats">
        <div className="stat"><div className="k">Expectancy per signal</div><div className={"v " + (Number(s.expectancy) > 0 ? "green" : "red")}>{s.expectancy ?? "—"}R</div><div className="sub">{s.closed} closed of {s.signals} signals</div></div>
        <div className="stat"><div className="k">Win rate</div><div className="v">{s.winRate ?? "—"}%</div><div className="sub">edge needs win% × avgW &gt; loss% × 1R</div></div>
        <div className="stat"><div className="k">Profit factor</div><div className={"v " + (Number(s.pf) >= 1.5 ? "green" : "amber")}>{s.pf ?? "—"}</div><div className="sub">gross wins ÷ gross losses (R)</div></div>
        <div className="stat"><div className="k">Trigger rate</div><div className="v blue">{s.triggerRate ?? "—"}%</div><div className="sub">avg {s.avgDays ?? "—"} days to trigger</div></div>
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <h3>Cumulative R — {label} (closed signals, by signal date)</h3>
        <LineChart points={curve} color={cum >= 0 ? "var(--green)" : "var(--red)"} fmt={(v) => v.toFixed(1) + "R"} />
      </div>

      {typeSplits.length > 0 && (
        <div className="stats" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
          {typeSplits.map(([name, subset]) => {
            const t = agg(subset);
            return (
              <div className="stat" key={name}>
                <div className="k">{name}</div>
                <div className="v" style={{ fontSize: 20 }}>{t.expectancy ?? "—"}R</div>
                <div className="sub">{t.winRate ?? "—"}% win · PF {t.pf ?? "—"} · n={t.closed}</div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default async function Performance() {
  const [bundle, rows, weekly] = await Promise.all([latestBundle(), signalOutcomes(), latestReview()]);
  const regime = bundle?.run?.regime || null;
  const rv = weekly?.review;

  // US only since the moomoo migration (Bursa data is unavailable via OpenD)
  // structure and behavior differ too much for a blended expectancy to mean much
  const markets = [
    ["United States", "us", rows.filter((r) => r.market === "US")],
  ].filter(([, , subset]) => subset.length > 0);

  const oTag = (o) => {
    const map = { win: "good", loss: "bad", open: "pivot", never_triggered: "neutral" };
    return <span className={`tag ${map[o] || "neutral"}`}>{o.replace("_", " ")}</span>;
  };

  return (
    <Shell regime={regime} asOf={bundle?.run?.run_date?.slice(0, 10)} active="/performance">
      <div className="page-head">
        <div>
          <h1>Receipts</h1>
          <div className="asof">Every past swing/watchlist signal replayed against real price history — trigger within 15 sessions, stop-first grading, 2R target. No cherry-picking: never-triggered signals stay on the record.</div>
        </div>
      </div>

      {rv && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>
            Weekly review <span className="tag neutral">AI — hypotheses, not instructions</span>
            <span className="tag us">{String(weekly.created_at).slice(0, 10)}</span>
          </h3>
          {rv.summary && <div className="brief-headline">{rv.summary}</div>}
          <div className="brief-grid">
            {(rv.working || []).length > 0 && (
              <div>
                <div className="rsec-t">Working</div>
                {rv.working.map((w, i) => (
                  <div className="brief-counter" key={i}><span className="imp good">▲</span><span /><span className="s-why">{w}</span></div>
                ))}
              </div>
            )}
            {(rv.not_working || []).length > 0 && (
              <div>
                <div className="rsec-t">Not working</div>
                {rv.not_working.map((w, i) => (
                  <div className="brief-counter" key={i}><span className="imp bad">▼</span><span /><span className="s-why">{w}</span></div>
                ))}
              </div>
            )}
          </div>
          {(rv.hypotheses || []).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="rsec-t">Hypotheses to test</div>
              {rv.hypotheses.map((h, i) => (
                <div className="rsec plan" key={i}>
                  <div className="rsec-l"><b>{h.hypothesis}</b></div>
                  {h.evidence && <div className="rsec-l">Evidence: {h.evidence}</div>}
                  {h.how_to_test && <div className="rsec-l">Test: {h.how_to_test}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {markets.length === 0 && (
        <div className="panel"><h3>No graded signals yet</h3><div className="reasoning">Receipts accumulate after each nightly scan.</div></div>
      )}
      {markets.map(([label, tagClass, subset]) => (
        <MarketSection key={tagClass} label={label} tagClass={tagClass} rows={subset} />
      ))}

      <div className="tbl-wrap" style={{ marginTop: 14 }}>
        <table className="screener">
          <thead><tr><th>Signal date</th><th>Ticker</th><th>Mkt</th><th>Type</th><th>Trigger</th><th>Stop</th><th>Outcome</th><th>R</th><th>Days to trigger</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan="9" className="empty">No graded signals yet — receipts accumulate after each nightly scan.</td></tr>}
            {[...rows].reverse().map((r, i) => (
              <tr key={i} style={{ cursor: "default" }}>
                <td className="num">{r.signal_date?.slice(0, 10)}</td>
                <td className="sym">{r.ticker.replace(".KL", "")}</td>
                <td>{r.signal_type.replace("_", " ")}</td>
                <td className="num">{Number(r.trigger_price).toFixed(2)}</td>
                <td className="num">{Number(r.stop_price).toFixed(2)}</td>
                <td>{oTag(r.outcome)}</td>
                <td className="num" style={{ color: Number(r.r_multiple) > 0 ? "var(--green)" : Number(r.r_multiple) < 0 ? "var(--red)" : "var(--dim)" }}>{r.r_multiple != null ? Number(r.r_multiple).toFixed(2) : "—"}</td>
                <td className="num">{r.days_to_trigger ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}
