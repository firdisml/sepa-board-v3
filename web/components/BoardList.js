"use client";
import { money } from "@/lib/format";

function Dots({ checks, setup }) {
  const entries = Object.values(checks || {});
  const passed = setup?.rules_passed ?? entries.filter((c) => c.pass).length;
  const total = setup?.rules_total ?? entries.length;
  return (
    <span className="dots">
      {entries.map((c, i) => <span key={i} className={"dot" + (c.pass ? " ok" : "")} />)}
      <span className="frac">{passed}/{total}</span>
    </span>
  );
}

const BUCKET = {
  swing: ["good", "Swing"], watchlist: ["pivot", "Watch"],
  position: ["neutral", "Position"], forming: ["neutral", "Forming"],
};

// The board's sidebar: five columns only (Symbol, Bucket, Price, RS*,
// Trend Template) — everything else that used to live in this table (VCP,
// quality, %-off-high/low, ADR, NI YoY, pivot, stop) now belongs in the
// detail pane once a row is selected, not in a list you're scanning for
// the next name to check.
export default function BoardList({ rows, selected, onSelect }) {
  return (
    <div className="board-list">
      <table>
        <thead>
          <tr>
            <th>Symbol</th><th>Bucket</th><th>Price</th>
            <th title="Percentile WITHIN the nightly moomoo funnel (~200 pre-screened strong names), not across the whole market.">RS*</th>
            <th>Trend</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan="5" className="empty">No counters match.</td></tr>
          )}
          {rows.map((r) => {
            const [cls, label] = BUCKET[r.bucket] || ["neutral", r.bucket];
            return (
              <tr key={r.ticker + r.market}
                  className={r.ticker === selected ? "selected" : ""}
                  onClick={() => onSelect(r.ticker)}>
                <td><div className="sym">{r.ticker}</div><div className="sym-name">{r.name || ""}</div></td>
                <td><span className={`tag ${cls}`}>{label}</span></td>
                <td className="num">{money(r.price, r.market)}</td>
                <td><span className={"rs " + ((r.rs_rank ?? 0) >= 90 ? "hot" : (r.rs_rank ?? 0) >= 70 ? "ok" : "weak")}>{r.rs_rank}</span></td>
                <td><Dots checks={r.checks} setup={r.setup} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
