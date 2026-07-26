"use client";
import { money } from "@/lib/format";

/* Left rail — the watchlist. Compact rows: ticker over name on the left,
   price over the number that decides whether you care on the right.

   Order is action-first even though the shape is a flat list: BUY sits above
   CLOSE TO READY, which sits above the rest. A section header is one dim
   uppercase line, not a panel — the rail must stay scannable in one pass. */

const GROUPS = [
  // "Buy now" used to cover the whole swing bucket, INCLUDING counters still
  // below their pivot — which are not buyable at all until price crosses.
  // Entry was always the pivot, so the plan was right; only the label lied.
  { key: "triggered", label: "Triggered · buy at open", tone: "g" },
  { key: "atBuyPoint", label: "At buy point · buy-stop at pivot", tone: "g" },
  { key: "close", label: "Close to ready", tone: "y" },
  { key: "watchlist", label: "Watching", tone: "" },
  { key: "position", label: "Position", tone: "" },
  { key: "forming", label: "Forming", tone: "" },
];

function RailRow({ r, selected, onSelect }) {
  const toPiv = r.pivot ? (r.price / r.pivot - 1) * 100 : null;
  const rsCls = (r.rs_rank ?? 0) >= 90 ? "hot" : (r.rs_rank ?? 0) >= 70 ? "ok" : "weak";
  return (
    <button
      className={"rail-row" + (r.ticker === selected ? " on" : "")}
      onClick={() => onSelect(r.ticker)}
    >
      <span className="rr-l">
        <span className="rr-sym">{r.ticker.replace(".KL", "")}</span>
        <span className="rr-name">{r.name || "—"}</span>
      </span>
      <span className="rr-r">
        <span className="rr-px">{money(r.price, r.market)}</span>
        <span className="rr-sub">
          <span className={"rs " + rsCls}>{r.rs_rank ?? "—"}</span>
          {toPiv != null && (
            <span className={"rr-piv" + (Math.abs(toPiv) <= 3 ? " near" : "")}>
              {toPiv > 0 ? "+" : ""}{toPiv.toFixed(1)}%
            </span>
          )}
        </span>
      </span>
      {/* tag strip: the setups that change what you'd do tomorrow */}
      <span className="rr-tags">
        {/* EP backtests -0.38R on Bursa (PLAN §12.1) — it is a hazard flag, so
            it wears the warning colour, not the "best setup" highlight */}
        {r.setup?.episodic_pivot && <i className="tag bad" title="Episodic pivot — backtests negative on Bursa; verify catalyst">EP?</i>}
        {r.setup?.ma20_bounce && <i className="tag good">20</i>}
        {r.setup?.ma50_bounce && <i className="tag good">50</i>}
        {r.vcp?.vcp && <i className="tag good">VCP</i>}
        {/* fundamentals grade, same colour mapping StockDetail uses. Absent
            when withheld — a counter that could not be graded shows no tag
            rather than a neutral one that would read as "graded, mediocre". */}
        {r.fundamentals?.grade && (
          <i className={"tag " + ({ A: "good", B: "good", C: "neutral",
                                    D: "bad", E: "bad" }[r.fundamentals.grade] || "neutral")}
             title={`Fundamentals grade ${r.fundamentals.grade}`}>
            {r.fundamentals.grade}
          </i>
        )}
        {r.extended && <i className="tag bad">EXT</i>}
      </span>
    </button>
  );
}

export default function Rail({ groups, selected, onSelect, q, setQ, counts }) {
  return (
    <div className="rail">
      <div className="rail-head">
        <input className="rail-search" placeholder="Search…" value={q}
               onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="rail-body">
        {GROUPS.map(({ key, label, tone }) => {
          const list = groups[key] || [];
          // both buy-side groups render when empty: "nothing triggered today"
          // is information, not an absence worth hiding
          if (!list.length && key !== "triggered" && key !== "atBuyPoint") return null;
          return (
            <div key={key} className="rail-grp">
              <div className={"rail-grp-h" + (tone ? ` ${tone}` : "")}>
                <span>{label}</span><span className="n">{list.length}</span>
              </div>
              {list.length === 0 ? (
                <div className="rail-empty">
                  {key === "triggered"
                    ? "Nothing crossed its pivot today."
                    : "Nothing sitting at a buy point. Cash is a position."}
                </div>
              ) : list.map((r) => (
                <RailRow key={r.ticker + r.market} r={r} selected={selected} onSelect={onSelect} />
              ))}
            </div>
          );
        })}
      </div>
      <div className="rail-foot">{counts.shown} of {counts.total} counters</div>
    </div>
  );
}
