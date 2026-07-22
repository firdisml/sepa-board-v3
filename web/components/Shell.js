export default function Shell({ regime, asOf, active = "/", children }) {
  const my = regime?.MY, us = regime?.US;
  const box = (label, r) => {
    if (!r) return null;
    const light = r.light || "yellow";
    const b = r.breadth;
    const pct = b?.pct_above_200ma;
    const barCls = pct >= 60 ? "g" : pct >= 40 ? "a" : "r";
    return (
      <div className="regime-box" style={{ marginBottom: 8 }}>
        <div className={`light ${light}`}>● {label}</div>
        <div className="sub">{r.note || ""}</div>
        {r.exposure?.rule && (
          <div className="sub" style={{ marginTop: 3, fontWeight: 600, color: light === "green" ? "var(--green)" : light === "red" ? "var(--red)" : "var(--amber)" }}>
            ▶ {r.exposure.rule}
          </div>
        )}
        {Object.entries(r.indices || {}).map(([ix, h]) => (
          <div className="ix-row" key={ix}>
            <span className="ix">{ix.replace("^", "")}</span>
            <span className={"ma " + (h.above_50 ? "ok" : "no")}>50</span>
            <span className={"ma " + (h.above_200 ? "ok" : "no")}>200</span>
            {h.dist_days != null && (
              <span className={"dd" + (h.dist_days >= 5 ? " hot" : "")} title="distribution days (25 sessions)">
                {h.dist_days}D
              </span>
            )}
            {h.follow_through?.recent && <span className="ftd" title="recent follow-through day">FTD</span>}
          </div>
        ))}
        {b && (
          <>
            <div className="bbar" title="% of universe above its 200-day MA">
              <span className={barCls} style={{ width: `${Math.max(2, Math.min(100, pct || 0))}%` }} />
            </div>
            <div className="sub">{pct}% &gt;200MA · {b.pct_above_50ma}% &gt;50MA · {b.new_highs}H/{b.new_lows}L</div>
          </>
        )}
      </div>
    );
  };
  const links = [["/", "Screener"], ["/performance", "Receipts"], ["/backtest", "Backtests"]];
  return (
    <div className="shell">
      <aside className="sidebar">
        <div>
          <div className="brand">SEPA <span className="tick">Board</span></div>
          <div className="brand-sub">Minervini screener · US</div>
        </div>
        <nav className="side-nav">
          {links.map(([href, label]) => (
            <a key={href} href={href} className={active === href ? "active" : ""}>{label}</a>
          ))}
        </nav>
        <div className="side-label">Market regime</div>
        {box("US — SPY/QQQ", us)}
        <div className="side-foot">Scan: {asOf || "no data yet"}<br />Not financial advice.</div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
