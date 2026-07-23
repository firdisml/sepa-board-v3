"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import BoardList from "@/components/BoardList";
import StockDetail from "@/components/StockDetail";
import Brief from "@/components/Brief";

// filters survive re-selection (per-tab; a fresh tab starts clean).
// localStorage would resurrect stale filters days later and make the board
// look mysteriously empty.
const FILTER_KEY = "sepa-screener-filters";

// One page: the board list stays mounted in its own sidebar column and a
// click fetches that counter's detail into the pane beside it — no
// navigation, so there is nothing to "go back" from.
export default function Board({ run, candidates, regime, btByMarket }) {
  const [minRS, setMinRS] = useState(70);
  const [vcpOnly, setVcpOnly] = useState(false);
  const [fullOnly, setFullOnly] = useState(false);
  const [q, setQ] = useState("");
  // top of the board-sorted list by default — same value on server and first
  // client render (both read the same `candidates` prop), so there is no
  // hydration mismatch and no flash of an empty pane before the mount effect
  // below can apply a ?t= deep link.
  const [selected, setSelected] = useState(() => candidates?.[0]?.ticker ?? null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const restored = useRef(false);

  const rows = useMemo(() => (candidates || []).map((c) => ({
    ...c,
    passAll: Object.values(c.checks || {}).every((x) => x.pass),
    isVcp: !!c.vcp?.vcp,
  })), [candidates]);

  const filtered = rows.filter((r) =>
    (r.rs_rank ?? 0) >= minRS &&
    (!vcpOnly || r.isVcp) &&
    (!fullOnly || r.passAll) &&
    (q === "" || r.ticker.toLowerCase().includes(q.toLowerCase()) ||
      (r.name || "").toLowerCase().includes(q.toLowerCase()))
  );

  // restore AFTER mount (server HTML renders the defaults, so reading
  // storage/location during the first render would be a hydration mismatch).
  // ?t=TICKER makes a selection shareable; absent that, land on the top pick
  // so the page is never a blank pane on first load.
  useEffect(() => {
    try {
      const s = JSON.parse(sessionStorage.getItem(FILTER_KEY) || "null");
      if (s) {
        setMinRS(Number.isFinite(s.minRS) ? s.minRS : 70);
        setVcpOnly(!!s.vcpOnly);
        setFullOnly(!!s.fullOnly);
        setQ(s.q ?? "");
      }
    } catch { /* corrupt storage — keep defaults */ }
    restored.current = true;
    const t = new URLSearchParams(window.location.search).get("t");
    setSelected(t || rows[0]?.ticker || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!restored.current) return;
    try {
      sessionStorage.setItem(FILTER_KEY, JSON.stringify({ minRS, vcpOnly, fullOnly, q }));
    } catch { /* private mode — filters just won't persist */ }
  }, [minRS, vcpOnly, fullOnly, q]);

  // plain history.replaceState, not next/navigation's router: this is a
  // client-only bookmark update, and routing it through the router would
  // risk a server re-render of a page that already has everything it needs.
  useEffect(() => {
    if (!selected) return;
    window.history.replaceState(null, "", `/?t=${encodeURIComponent(selected)}`);
    let cancelled = false;
    setLoading(true);
    fetch(`/api/stock/${encodeURIComponent(selected)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled) { setDetail(d); setLoading(false); } })
      .catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);

  const passing = rows.filter((r) => r.passAll).length;
  const vcps = rows.filter((r) => r.isVcp).length;
  const swings = rows.filter((r) => r.bucket === "swing").length;
  const avgRS = passing
    ? Math.round(rows.filter((r) => r.passAll).reduce((s, r) => s + (r.rs_rank || 0), 0) / passing)
    : "—";

  const maturing = rows
    .filter((r) => r.setup?.anticipation)
    .sort((a, b) => (b.setup.anticipation.score || 0) - (a.setup.anticipation.score || 0))
    .slice(0, 10);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Screener</h1>
          <div className="asof">as of {run?.run_date?.slice(0, 10)}</div>
        </div>
        <input className="search" placeholder="Search symbol or name…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <Brief brief={run?.ai_brief} onSelect={setSelected} />

      <div className="stats">
        <div className="stat"><div className="k">Trend Template pass</div><div className="v green">{passing}</div><div className="sub">of {rows.length} candidates on the board</div></div>
        <div className="stat"><div className="k">VCP setups live</div><div className="v amber">{vcps}</div><div className="sub">contractions + volume dry-up</div></div>
        <div className="stat"><div className="k">Swing-ready</div><div className="v blue">{swings}</div><div className="sub">VCP at/near pivot, not extended</div></div>
        <div className="stat"><div className="k">Avg RS (passing)</div><div className="v">{avgRS}</div><div className="sub">target ≥ 90 for leaders</div></div>
      </div>

      {maturing.length > 0 && (
        <div className="panel" style={{ marginBottom: 14, padding: "12px 18px" }}>
          <div className="rsec-t" style={{ marginBottom: 8 }}>🎯 Maturing setups — breakout anticipation (closest to ready first)</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {maturing.map((r) => (
              <button key={r.ticker + r.market} className="tkr" onClick={() => setSelected(r.ticker)}
                 title={`anticipation score ${r.setup.anticipation.score}/100`}>
                {r.ticker} · {r.setup.anticipation.pct_to_pivot}% to pivot
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="workbench">
        <div className="workbench-list">
          <div className="filters">
            <button className={"chip" + (fullOnly ? " on" : "")} onClick={() => setFullOnly(!fullOnly)}>Full template</button>
            <button className={"chip" + (vcpOnly ? " on" : "")} onClick={() => setVcpOnly(!vcpOnly)}>VCP only</button>
            <span className="rs-slider">
              RS ≥ <input type="range" min="0" max="99" value={minRS} onChange={(e) => setMinRS(+e.target.value)} />
              <span className="val">{minRS}</span>
            </span>
          </div>
          <BoardList rows={filtered} selected={selected} onSelect={setSelected} />
        </div>
        <div className="workbench-detail">
          {!selected && (
            <div className="panel">Pick a counter from the list to see its chart and plan.</div>
          )}
          {selected && !detail && loading && <div className="panel">Loading {selected}…</div>}
          {selected && !detail && !loading && (
            <div className="panel"><h3>{selected}</h3>
              <div className="reasoning">Not on the current board — it either failed the screen or hasn't been scanned.</div>
            </div>
          )}
          {detail && (
            <div style={{ opacity: loading ? 0.6 : 1, transition: "opacity .15s" }}>
              <StockDetail c={detail} regime={regime} latestRun={run?.run_date?.slice(0, 10)} btByMarket={btByMarket} />
            </div>
          )}
        </div>
      </div>
    </>
  );
}
