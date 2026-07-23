"use client";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { COLS, Cols, Head, Row } from "@/components/BoardTable";
import StockDetail from "@/components/StockDetail";
import Brief from "@/components/Brief";

const FILTER_KEY = "sepa-screener-filters";

/* ACTION-FIRST. The page answers "what do I do tomorrow" in reading order:
   regime (how much may I risk) -> buy points -> what is nearly ready -> the
   AI read -> everything else, collapsed.

   The previous layout led with a search box and a five-number stat line in
   which "0 swing-ready" — the actual answer — was the fourth item in grey.
   Sections now carry their own counts, and an empty BUY section says so in
   words rather than leaving you to infer it from a list that looks busy.

   Nothing is dropped to achieve this: every column the old screener had is
   still on every row (see BoardTable), and all sections share one fixed
   column grid so the whole page aligns as a single table. */
export default function Board({ run, candidates, regime, btByMarket }) {
  const [minRS, setMinRS] = useState(0);
  const [vcpOnly, setVcpOnly] = useState(false);
  const [fullOnly, setFullOnly] = useState(false);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState({ watch: true, position: false, forming: false });
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const restored = useRef(false);

  const rows = useMemo(() => (candidates || []).map((c) => {
    const toPiv = c.pivot ? (c.price / c.pivot - 1) * 100 : null;
    return {
      ...c,
      passAll: Object.values(c.checks || {}).every((x) => x.pass),
      isVcp: !!c.vcp?.vcp,
      toPiv,
      antic: c.setup?.anticipation?.score ?? null,
    };
  }), [candidates]);

  const match = (r) =>
    (r.rs_rank ?? 0) >= minRS &&
    (!vcpOnly || r.isVcp) &&
    (!fullOnly || r.passAll) &&
    (q === "" || r.ticker.toLowerCase().includes(q.toLowerCase()) ||
      (r.name || "").toLowerCase().includes(q.toLowerCase()));

  const visible = rows.filter(match);

  // BUY: at a buy point now. CLOSE: has a pivot and price is within reach of
  // it — sorted by the anticipation score when the engine computed one, else
  // by raw distance, so the closest thing to a decision sits at the top.
  const buy = visible.filter((r) => r.bucket === "swing");
  const close = visible
    .filter((r) => r.bucket !== "swing" && r.toPiv != null && r.toPiv > -12 && r.toPiv < 3)
    .sort((a, b) => (b.antic ?? -1) - (a.antic ?? -1) || Math.abs(a.toPiv) - Math.abs(b.toPiv))
    .slice(0, 12);
  const closeSet = new Set(close.map((r) => r.ticker));
  const rest = (bucket) => visible.filter((r) => r.bucket === bucket && !closeSet.has(r.ticker));

  useEffect(() => {
    try {
      const s = JSON.parse(sessionStorage.getItem(FILTER_KEY) || "null");
      if (s) {
        setMinRS(Number.isFinite(s.minRS) ? s.minRS : 0);
        setVcpOnly(!!s.vcpOnly); setFullOnly(!!s.fullOnly); setQ(s.q ?? "");
      }
    } catch { /* corrupt storage — keep defaults */ }
    restored.current = true;
    const t = new URLSearchParams(window.location.search).get("t");
    if (t) setSelected(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!restored.current) return;
    try {
      sessionStorage.setItem(FILTER_KEY, JSON.stringify({ minRS, vcpOnly, fullOnly, q }));
    } catch { /* private mode — filters just won't persist */ }
  }, [minRS, vcpOnly, fullOnly, q]);

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

  const pick = (t) => setSelected((cur) => (cur === t ? null : t));

  // one <table> per section, all sharing COLS — the expanded detail rides
  // inside the row it belongs to, so the chart never loses its context
  const Section = ({ id, title, note, list, tone, collapsible }) => {
    const isOpen = !collapsible || open[id];
    return (
      <section className={"bsec" + (tone ? ` ${tone}` : "")}>
        <header
          className={"bsec-h" + (collapsible ? " clickable" : "")}
          onClick={collapsible ? () => setOpen({ ...open, [id]: !isOpen }) : undefined}
        >
          {collapsible && <span className="caret">{isOpen ? "▾" : "▸"}</span>}
          <h2>{title}</h2>
          <span className="bsec-n">{list.length}</span>
          {note && <span className="bsec-note">{note}</span>}
        </header>
        {isOpen && (
          <div className="bt-wrap">
            <table className="bt">
              <Cols />
              <Head />
              <tbody>
                {list.length === 0 && (
                  <tr><td colSpan={COLS.length} className="empty">
                    {id === "buy"
                      ? "Nothing at a buy point today. Cash is a position."
                      : "No counters here."}
                  </td></tr>
                )}
                {list.map((r) => (
                  <Fragment key={r.ticker + r.market}>
                    <Row r={r} selected={selected} onSelect={pick} />
                    {selected === r.ticker && (
                      <tr className="detail-row">
                        <td colSpan={COLS.length}>
                          {loading && !detail && <div className="panel">Loading {selected}…</div>}
                          {detail && (
                            <StockDetail c={detail} regime={regime}
                              latestRun={run?.run_date?.slice(0, 10)} btByMarket={btByMarket} />
                          )}
                          {!loading && !detail && (
                            <div className="panel">No stored detail for {selected}.</div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    );
  };

  const hasBrief = Object.keys(run?.ai_brief || {}).some(
    (m) => run.ai_brief[m] && typeof run.ai_brief[m] === "object");

  return (
    <div className="dash">
      <div className="dash-topbar">
        <input className="search" placeholder="Search symbol or name…"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <button className={"chip" + (fullOnly ? " on" : "")}
                onClick={() => setFullOnly(!fullOnly)}>Full template</button>
        <button className={"chip" + (vcpOnly ? " on" : "")}
                onClick={() => setVcpOnly(!vcpOnly)}>VCP only</button>
        <span className="rs-slider">
          RS ≥ <input type="range" min="0" max="99" value={minRS}
                      onChange={(e) => setMinRS(+e.target.value)} />
          <span className="val">{minRS}</span>
        </span>
        <span className="asof">{visible.length} of {rows.length} · as of {run?.run_date?.slice(0, 10)}</span>
      </div>

      <Section id="buy" title="Buy tomorrow" tone="act" list={buy}
               note="at a buy point — entry, stop and size on the row" />

      <Section id="close" title="Close to ready" tone="near" list={close}
               note="base built, price approaching the pivot" />

      {hasBrief && (
        <section className="bsec">
          <header className="bsec-h"><h2>AI read</h2></header>
          <Brief brief={run?.ai_brief} onSelect={pick} />
        </section>
      )}

      <Section id="watch" title="Watching" list={rest("watchlist")} collapsible />
      <Section id="position" title="Position — trend intact, no base" list={rest("position")} collapsible />
      <Section id="forming" title="Forming — near-miss template" list={rest("forming")} collapsible />
    </div>
  );
}
