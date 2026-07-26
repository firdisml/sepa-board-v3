"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Rail from "@/components/Rail";
import SidePanel from "@/components/SidePanel";
import StockDetail from "@/components/StockDetail";
import Brief from "@/components/Brief";

const FILTER_KEY = "sepa-screener-filters";

/* TERMINAL LAYOUT — rail | chart | stats, each pane scrolling on its own and
   the page itself never scrolling. A trading screen is a place you look at,
   not a document you read top to bottom, and the previous stacked-section
   build made you scroll past three sections to reach a chart.

   Action-first survives inside the shape: the rail is ordered Buy -> Close to
   ready -> Watching -> Position -> Forming, so the first thing your eye lands
   on is still the only question that matters at 8:30pm. */
export default function Board({ run, candidates, regime, btByMarket, btByStrategy }) {
  const [q, setQ] = useState("");
  const [market, setMarket] = useState("ALL");
  const [minRS, setMinRS] = useState(0);
  const [vcpOnly, setVcpOnly] = useState(false);
  const [gradeA, setGradeA] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showBrief, setShowBrief] = useState(false);
  const restored = useRef(false);

  const rows = useMemo(() => (candidates || []).map((c) => ({
    ...c,
    isVcp: !!c.vcp?.vcp,
    toPiv: c.pivot ? (c.price / c.pivot - 1) * 100 : null,
    antic: c.setup?.anticipation?.score ?? null,
  })), [candidates]);

  // markets actually on tonight's board — the toggle only appears when there
  // is something to toggle between, so a Bursa-only night looks unchanged
  const markets = useMemo(
    () => [...new Set((rows || []).map((r) => r.market).filter(Boolean))].sort(),
    [rows]);

  const visible = rows.filter((r) =>
    (market === "ALL" || r.market === market) &&
    (r.rs_rank ?? 0) >= minRS &&
    (!vcpOnly || r.isVcp) &&
    // Grade A only. Note this also hides every WITHHELD grade — correct, since
    // "show me A-graded names" cannot include ones that were never gradeable
    // (pre-revenue biotech, IFRS foreign filers), but it does mean the filter
    // is stricter than it looks on a biotech-heavy US board.
    (!gradeA || r.fundamentals?.grade === "A") &&
    (q === "" || r.ticker.toLowerCase().includes(q.toLowerCase()) ||
      (r.name || "").toLowerCase().includes(q.toLowerCase())));

  const groups = useMemo(() => {
    // The swing bucket holds two situations that need OPPOSITE actions, and
    // lumping them under one "Buy now" heading is what made the board read as
    // "buy these tomorrow" when most were not yet buyable:
    //   triggered  — price has crossed the pivot; actionable at the next open
    //   at-buy-point — still below it; the entry is a BUY-STOP at the pivot,
    //                  and nothing happens until price gets there
    // Entry price is the pivot in both cases, so the plan was always right —
    // only the label was lying.
    const swing = visible.filter((r) => r.bucket === "swing");
    const triggered = swing.filter((r) => r.toPiv != null && r.toPiv >= 0);
    const atBuyPoint = swing.filter((r) => !(r.toPiv != null && r.toPiv >= 0));
    const close = visible
      .filter((r) => r.bucket !== "swing" && r.toPiv != null && r.toPiv > -12 && r.toPiv < 3)
      .sort((a, b) => (b.antic ?? -1) - (a.antic ?? -1) || Math.abs(a.toPiv) - Math.abs(b.toPiv));
    const taken = new Set([...swing, ...close].map((r) => r.ticker));
    const rest = (b) => visible.filter((r) => r.bucket === b && !taken.has(r.ticker));
    return { triggered, atBuyPoint, close, watchlist: rest("watchlist"),
             position: rest("position"), forming: rest("forming") };
  }, [visible]);

  // land on something actionable rather than a blank pane
  useEffect(() => {
    try {
      const s = JSON.parse(sessionStorage.getItem(FILTER_KEY) || "null");
      if (s) { setMinRS(s.minRS ?? 0); setVcpOnly(!!s.vcpOnly); setQ(s.q ?? "");
               setMarket(s.market ?? "ALL"); setGradeA(!!s.gradeA); }
    } catch { /* corrupt storage — keep defaults */ }
    restored.current = true;
    const t = new URLSearchParams(window.location.search).get("t");
    setSelected(t || groups.triggered[0]?.ticker || groups.atBuyPoint[0]?.ticker
                || groups.close[0]?.ticker || rows[0]?.ticker || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!restored.current) return;
    try {
      sessionStorage.setItem(FILTER_KEY, JSON.stringify({ minRS, vcpOnly, q, market, gradeA }));
    } catch { /* private mode — filters just won't persist */ }
  }, [minRS, vcpOnly, q, market, gradeA]);

  // Switching market strands the selection in a set the rail no longer shows,
  // leaving a Bursa counter open while the rail lists only US. Re-land on
  // something actionable in the market you just switched to. Scoped to
  // `market` deliberately: nudging RS or search should NOT move your selection.
  useEffect(() => {
    if (!restored.current || !selected) return;
    if (visible.some((r) => r.ticker === selected)) return;
    setSelected(groups.triggered[0]?.ticker || groups.atBuyPoint[0]?.ticker
                || groups.close[0]?.ticker || visible[0]?.ticker || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market]);

  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    window.history.replaceState(null, "", `/?t=${encodeURIComponent(selected)}`);
    let cancelled = false;
    setLoading(true);
    fetch(`/api/stock/${encodeURIComponent(selected)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled) { setDetail(d); setLoading(false); } })
      .catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);

  const hasBrief = Object.keys(run?.ai_brief || {}).some(
    (m) => run.ai_brief[m] && typeof run.ai_brief[m] === "object");

  return (
    <div className="term">
      <Rail groups={groups} selected={selected} onSelect={setSelected} q={q} setQ={setQ}
            counts={{ shown: visible.length, total: rows.length }} />

      <div className="term-main">
        <div className="term-bar">
          {markets.length > 1 && (
            <span className="mkt-switch">
              {["ALL", ...markets].map((m) => (
                <button key={m} className={"chip" + (market === m ? " on" : "")}
                        onClick={() => setMarket(m)}>
                  {m === "ALL" ? `All ${rows.length}` :
                    `${m} ${rows.filter((r) => r.market === m).length}`}
                </button>
              ))}
            </span>
          )}
          <button className={"chip" + (vcpOnly ? " on" : "")}
                  onClick={() => setVcpOnly(!vcpOnly)}>VCP only</button>
          <button className={"chip" + (gradeA ? " on" : "")}
                  onClick={() => setGradeA(!gradeA)}
                  title="Fundamentals grade A only — hides withheld grades too">
            Grade A {rows.filter((r) => r.fundamentals?.grade === "A").length}
          </button>
          <span className="rs-slider">
            RS ≥ <input type="range" min="0" max="99" value={minRS}
                        onChange={(e) => setMinRS(+e.target.value)} />
            <span className="val">{minRS}</span>
          </span>
          {hasBrief && (
            <button className={"chip" + (showBrief ? " on" : "")}
                    onClick={() => setShowBrief(!showBrief)}>AI brief</button>
          )}
          <span className="asof">as of {run?.run_date?.slice(0, 10)}</span>
        </div>

        <div className="term-scroll">
          {showBrief && hasBrief && (
            <div className="term-brief"><Brief brief={run?.ai_brief} onSelect={setSelected} /></div>
          )}
          {loading && !detail && <div className="term-empty">Loading {selected}…</div>}
          {!loading && !detail && <div className="term-empty">Pick a counter from the rail.</div>}
          {detail && (
            <div style={{ opacity: loading ? 0.55 : 1, transition: "opacity .12s" }}>
              <StockDetail c={detail} regime={regime}
                latestRun={run?.run_date?.slice(0, 10)} btByMarket={btByMarket}
                btByStrategy={btByStrategy} />
            </div>
          )}
        </div>
      </div>

      <SidePanel c={detail} />
    </div>
  );
}
