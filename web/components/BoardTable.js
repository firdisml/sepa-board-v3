"use client";
import { money, price as fmtPrice } from "@/lib/format";

/* One table shape, used by every section of the board.
   ALIGNMENT IS THE POINT: `table-layout: fixed` plus an identical <colgroup>
   in every instance means the BUY table, the WATCHING table and the FORMING
   table share one grid — columns line up down the whole page, not just within
   a section. Numbers are tabular and right-aligned; text is left-aligned;
   nothing is centred, because centred columns wander as values change width. */

export const COLS = [
  { w: "168px", k: "sym", label: "Symbol", align: "left" },
  { w: "150px", k: "setup", label: "Setup", align: "left" },
  { w: "84px", k: "price", label: "Price", align: "right" },
  // v2 shipped this as "RS*" with a footnote warning it was only a percentile
  // within a ~200-name pre-screened funnel. v3 ranks the FULL exchange (1,029
  // counters) before any liquidity filter, so the asterisk and its apology are
  // gone — this is a real market-wide rank now.
  { w: "48px", k: "rs", label: "RS", align: "right" },
  { w: "96px", k: "trend", label: "Trend", align: "left" },
  { w: "78px", k: "vcp", label: "VCP", align: "left" },
  { w: "44px", k: "q", label: "Q", align: "right" },
  { w: "72px", k: "offhi", label: "% off Hi", align: "right" },
  { w: "72px", k: "abvlo", label: "% abv Lo", align: "right" },
  { w: "58px", k: "adr", label: "ADR%", align: "right" },
  { w: "76px", k: "ni", label: "NI YoY", align: "right" },
  { w: "84px", k: "pivot", label: "Pivot", align: "right" },
  { w: "66px", k: "topiv", label: "→ Piv", align: "right" },
  { w: "84px", k: "stop", label: "Stop", align: "right" },
];

const BUCKET = {
  swing: ["good", "Swing"], watchlist: ["pivot", "Watch"],
  position: ["neutral", "Position"], forming: ["neutral", "Forming"],
};

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

export function Cols() {
  return <colgroup>{COLS.map((c) => <col key={c.k} style={{ width: c.w }} />)}</colgroup>;
}

export function Head() {
  return (
    <thead>
      <tr>
        {COLS.map((c) => (
          <th key={c.k} className={c.align === "right" ? "ta-r" : "ta-l"}>{c.label}</th>
        ))}
      </tr>
    </thead>
  );
}

export function Row({ r, selected, onSelect }) {
  const [cls, label] = BUCKET[r.bucket] || ["neutral", r.bucket];
  const offHi = r.checks?.within_25pct_of_52w_high?.pct_below_high;
  const abvLo = r.checks?.above_52w_low_30pct?.pct_above_low;
  const ni = r.fundamentals?.ni_yoy_pct;
  // distance to the buy point: the one number that says "how close is this?"
  const toPiv = r.pivot ? ((r.price / r.pivot - 1) * 100) : null;

  return (
    <tr className={r.ticker === selected ? "selected" : ""}
        onClick={() => onSelect(r.ticker)}>
      <td className="ta-l">
        <span className="sym">{r.ticker}</span>
        {r.setup?.ipo ? <span className="tag ipo">IPO</span> : null}
        <span className="sym-name">{r.name || ""}</span>
      </td>
      <td className="ta-l tagcell">
        <span className={`tag ${cls}`}>{label}</span>
        {r.setup?.ma20_bounce ? <span className="tag good">20MA</span> : null}
        {r.setup?.ma50_bounce ? <span className="tag good">50MA</span> : null}
        {r.setup?.episodic_pivot ? <span className="tag ep">EP</span> : null}
        {r.setup?.momentum_burst ? <span className="tag pivot">4%</span> : null}
        {r.setup?.pocket_pivot ? <span className="tag good">PP</span> : null}
        {r.extended ? <span className="tag bad">Ext</span> : null}
      </td>
      <td className="ta-r num">{money(r.price, r.market)}</td>
      <td className="ta-r">
        <span className={"rs " + ((r.rs_rank ?? 0) >= 90 ? "hot" : (r.rs_rank ?? 0) >= 70 ? "ok" : "weak")}>
          {r.rs_rank ?? "—"}
        </span>
      </td>
      <td className="ta-l"><Dots checks={r.checks} setup={r.setup} /></td>
      <td className="ta-l">
        {r.vcp?.vcp
          ? <span className="tag good">{(r.vcp.contractions_pct || []).length}T{r.vcp.volume_dry_up ? "·dry" : ""}</span>
          : (r.vcp?.contractions_pct?.length >= 2
              ? <span className="tag neutral">base</span>
              : <span className="dash">—</span>)}
      </td>
      <td className="ta-r num">{r.quality ?? "—"}</td>
      <td className={"ta-r num" + (offHi != null && offHi > 25 ? " neg" : "")}>
        {offHi != null ? `−${Math.round(offHi)}%` : "—"}
      </td>
      <td className={"ta-r num" + (abvLo != null && abvLo >= 30 ? " pos" : "")}>
        {abvLo != null ? `+${Math.round(abvLo)}%` : "—"}
      </td>
      <td className="ta-r num">{r.adr_pct ?? "—"}</td>
      <td className={"ta-r num" + (ni == null ? "" : ni >= 0 ? " pos" : " neg")}>
        {ni == null ? "—" : `${ni > 0 ? "+" : ""}${Math.round(ni)}%`}
        {r.fundamentals?.accelerating ? "▲" : ""}
      </td>
      <td className="ta-r num pivot-v">{r.pivot ? fmtPrice(r.pivot, r.market) : "—"}</td>
      <td className={"ta-r num" + (toPiv == null ? "" : Math.abs(toPiv) <= 5 ? " pos" : "")}>
        {toPiv == null ? "—" : `${toPiv > 0 ? "+" : ""}${toPiv.toFixed(1)}%`}
      </td>
      <td className="ta-r num">{r.stop ? fmtPrice(r.stop, r.market) : "—"}</td>
    </tr>
  );
}

export default function BoardTable({ rows, selected, onSelect, empty }) {
  return (
    <div className="bt-wrap">
      <table className="bt">
        <Cols />
        <Head />
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={COLS.length} className="empty">{empty || "Nothing here."}</td></tr>
          )}
          {rows.map((r) => (
            <Row key={r.ticker + r.market} r={r} selected={selected} onSelect={onSelect} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
