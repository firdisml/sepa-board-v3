"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { money } from "@/lib/format";

// was: "$" + toFixed(v >= 1 ? 2 : 3) — hardcoded USD, and it discarded the
// market argument the call site was already passing
const fmt = (v, market) => money(v, market);

// filters survive stock-page detours via sessionStorage (per-tab; a fresh tab
// starts clean). localStorage would resurrect stale filters days later and
// make the board look mysteriously empty.
const FILTER_KEY = "sepa-screener-filters";

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

export default function Screener({ run, candidates }) {
  const router = useRouter();
  const [market, setMarket] = useState("ALL");
  const [minRS, setMinRS] = useState(70);
  const [vcpOnly, setVcpOnly] = useState(false);
  const [fullOnly, setFullOnly] = useState(false);
  const [q, setQ] = useState("");

  // restore AFTER mount (server HTML renders the defaults, so reading storage
  // during the first render would be a hydration mismatch)
  const restored = useRef(false);
  useEffect(() => {
    try {
      const s = JSON.parse(sessionStorage.getItem(FILTER_KEY) || "null");
      if (s) {
        setMarket(s.market ?? "ALL");
        setMinRS(Number.isFinite(s.minRS) ? s.minRS : 70);
        setVcpOnly(!!s.vcpOnly);
        setFullOnly(!!s.fullOnly);
        setQ(s.q ?? "");
      }
    } catch { /* corrupt storage — keep defaults */ }
    restored.current = true;
  }, []);
  useEffect(() => {
    if (!restored.current) return;
    try {
      sessionStorage.setItem(FILTER_KEY, JSON.stringify({ market, minRS, vcpOnly, fullOnly, q }));
    } catch { /* private mode — filters just won't persist */ }
  }, [market, minRS, vcpOnly, fullOnly, q]);

  const rows = useMemo(() => (candidates || []).map((c) => ({
    ...c,
    passAll: Object.values(c.checks || {}).every((x) => x.pass),
    isVcp: !!c.vcp?.vcp,
    offHigh: c.checks?.within_25pct_of_52w_high?.pct_below_high,
    aboveLow: c.checks?.above_52w_low_30pct?.pct_above_low,
  })), [candidates]);

  const filtered = rows.filter((r) =>
    (r.rs_rank ?? 0) >= minRS &&
    (!vcpOnly || r.isVcp) &&
    (!fullOnly || r.passAll) &&
    (q === "" || r.ticker.toLowerCase().includes(q.toLowerCase()) ||
      (r.name || "").toLowerCase().includes(q.toLowerCase()))
  );

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

  const bucketTag = (b) => {
    const map = { swing: ["good", "Swing"], watchlist: ["pivot", "Watch"], position: ["neutral", "Position"], forming: ["neutral", "Forming"] };
    const [cls, label] = map[b] || ["neutral", b];
    return <span className={`tag ${cls}`}>{label}</span>;
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Screener</h1>
          <div className="asof">as of {run?.run_date?.slice(0, 10)}</div>
        </div>
        <input className="search" placeholder="Search symbol or name…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

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
              <a key={r.ticker + r.market} className="tkr" href={`/stock/${encodeURIComponent(r.ticker)}`}
                 title={`anticipation score ${r.setup.anticipation.score}/100`}>
                {r.ticker} · {r.setup.anticipation.pct_to_pivot}% to pivot
              </a>
            ))}
          </div>
        </div>
      )}

      <div className="filters">
        <button className={"chip" + (fullOnly ? " on" : "")} onClick={() => setFullOnly(!fullOnly)}>Full template only</button>
        <button className={"chip" + (vcpOnly ? " on" : "")} onClick={() => setVcpOnly(!vcpOnly)}>VCP only</button>
        <span className="rs-slider">
          RS ≥ <input type="range" min="0" max="99" value={minRS} onChange={(e) => setMinRS(+e.target.value)} />
          <span className="val">{minRS}</span>
        </span>
      </div>

      <div className="tbl-wrap">
        <table className="screener">
          <thead>
            <tr>
              <th>Symbol</th><th>Bucket</th><th>Price</th><th title="Percentile WITHIN the nightly moomoo funnel (~200 pre-screened strong names), not across the whole market. A relative sort, not an IBD-style market-wide RS rank.">RS*</th>
              <th>Trend Template</th><th>VCP</th><th>Q</th><th>% off Hi</th>
              <th>% abv Lo</th><th>ADR%</th><th>NI YoY</th><th>Pivot</th><th>Stop</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan="13" className="empty">No counters match — loosen a filter, or the nightly scan hasn't run yet.</td></tr>
            )}
            {filtered.map((r) => (
              <tr key={r.ticker + r.market} onClick={() => router.push(`/stock/${encodeURIComponent(r.ticker)}`)}>
                <td><div className="sym">{r.ticker}{r.setup?.ipo ? <> <span className="tag ipo">IPO</span></> : null}</div><div className="sym-name">{r.name || ""}</div></td>
                <td>
                  {bucketTag(r.bucket)}
                  {r.setup?.ma20_bounce ? <> <span className="tag good">20MA</span></> : null}
                  {r.setup?.ma50_bounce ? <> <span className="tag good">50MA</span></> : null}
                  {r.setup?.episodic_pivot ? <> <span className="tag ipo">EP</span></> : null}
                  {r.setup?.momentum_burst ? <> <span className="tag pivot">4%</span></> : null}
                  {r.extended ? <> <span className="tag bad">Ext</span></> : null}
                </td>
                <td className="num">{fmt(r.price, r.market)}</td>
                <td><span className={"rs " + ((r.rs_rank ?? 0) >= 90 ? "hot" : (r.rs_rank ?? 0) >= 70 ? "ok" : "weak")}>{r.rs_rank}</span></td>
                <td><Dots checks={r.checks} setup={r.setup} /></td>
                <td>{r.isVcp
                  ? <span className="tag good">{(r.vcp.contractions_pct || []).length}T{r.vcp.volume_dry_up ? " · dry" : ""}</span>
                  : (r.vcp?.contractions_pct?.length >= 2 ? <span className="tag neutral">base</span> : <span style={{ color: "var(--faint)" }}>—</span>)}</td>
                <td className="num">{r.quality ?? "—"}</td>
                <td className="num" style={{ color: (r.offHigh ?? 99) <= 25 ? "var(--ink)" : "var(--red)" }}>{r.offHigh != null ? `−${r.offHigh}%` : "—"}</td>
                <td className="num" style={{ color: (r.aboveLow ?? 0) >= 30 ? "var(--green)" : "var(--dim)" }}>{r.aboveLow != null ? `+${Math.round(r.aboveLow)}%` : "—"}</td>
                <td className="num" >{r.adr_pct ?? "—"}</td>
                <td className="num" style={{ color: r.fundamentals?.ni_yoy_pct == null ? "var(--faint)" : r.fundamentals.ni_yoy_pct >= 0 ? "var(--green)" : "var(--red)" }}>
                  {r.fundamentals?.ni_yoy_pct == null ? "—" : `${r.fundamentals.ni_yoy_pct > 0 ? "+" : ""}${Math.round(r.fundamentals.ni_yoy_pct)}%`}
                  {r.fundamentals?.accelerating ? " ▲" : ""}
                </td>
                <td className="num" style={{ color: "var(--amber)" }}>{r.pivot ? fmt(r.pivot, r.market) : "—"}</td>
                <td className="num">{r.stop ? fmt(r.stop, r.market) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
