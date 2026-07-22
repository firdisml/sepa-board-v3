"use client";
import { useEffect, useState } from "react";
import { cash, ccy, lotSize, money } from "@/lib/format";

/* One-input position sizing: enter capital ONCE (remembered on this device),
   risk % is set automatically by the market regime's exposure ladder
   (green 1% / yellow 0.5% / red = no new entries), entry/stop come from the
   board. The "adjust" toggle exposes the old manual fields for edge cases. */
export default function Calculator({ entry: entry0, stop: stop0, market, exposure, light }) {
  const lot = lotSize(market);
  const [equity, setEquity] = useState("");
  const [adjust, setAdjust] = useState(false);
  const [entry, setEntry] = useState(entry0 || 0);
  const [stop, setStop] = useState(stop0 || 0);
  const [riskOverride, setRiskOverride] = useState("");

  useEffect(() => {
    try {
      const saved = localStorage.getItem("sb_capital");
      if (saved) setEquity(saved);
    } catch {}
  }, []);
  const saveEquity = (v) => {
    setEquity(v);
    try { localStorage.setItem("sb_capital", v); } catch {}
  };

  const autoRisk = exposure?.risk_pct ?? 1.0;
  const riskPct = riskOverride !== "" ? +riskOverride : autoRisk;
  const eq = +equity || 0;
  const e = +entry, s = +stop;
  const rps = e - s;
  const riskAmt = eq * (riskPct / 100);
  let shares = rps > 0 ? Math.floor(riskAmt / rps) : 0;
  shares = Math.floor(shares / lot) * lot;
  let posVal = shares * e;
  const maxVal = eq * 0.25;
  const capped = posVal > maxVal && maxVal > 0;
  if (capped) {
    shares = Math.floor(maxVal / e / lot) * lot;
    posVal = shares * e;
  }
  const posPct = eq > 0 ? (posVal / eq) * 100 : 0;
  const stopPct = e > 0 ? (rps / e) * 100 : 0;
  const t2 = e + 2 * rps, t3 = e + 3 * rps;
  // prices at market precision (a 0.605 stop and a 0.610 pivot must not both
  // render as 0.61); cash amounts stay at 2dp, since sub-cent totals are noise
  const f = (v) => money(v, market);
  const fc = (v) => cash(v, market);
  const regimeNote = { green: "green regime — full size", yellow: "yellow regime — half size",
                       red: "red regime — no new entries" }[light] || "regime unknown — default 1%";

  return (
    <div>
      <div className="calc-inputs" style={{ gridTemplateColumns: "1fr" }}>
        <div>
          <label>Your capital ({ccy(market)}) — remembered on this device</label>
          <input type="number" placeholder="e.g. 50000" value={equity}
                 onChange={(x) => saveEquity(x.target.value)} />
        </div>
      </div>

      {!eq ? (
        <div className="calc-row"><span className="k">Enter your capital once — everything else is automatic.</span></div>
      ) : riskPct === 0 && riskOverride === "" ? (
        <div className="calc-row"><span className="k" style={{ color: "var(--red)" }}>
          {regimeNote}. The ladder says manage exits only — no size suggested. (Use adjust to override.)
        </span></div>
      ) : rps <= 0 ? (
        <div className="calc-row"><span className="k">Stop must be below entry — use adjust to set them.</span></div>
      ) : (
        <>
          <div className="calc-row"><span className="k">Risk / trade (auto)</span>
            <span className="v">{riskPct}% · {riskOverride !== "" ? "manual override" : regimeNote}</span></div>
          <div className="calc-row"><span className="k">Entry / stop (board)</span>
            <span className="v">{f(e)} / {f(s)} (−{stopPct.toFixed(1)}%)</span></div>
          {stopPct > 8 && <div className="calc-row"><span className="k" style={{ color: "var(--red)" }}>
            Stop wider than 8% — outside Minervini's max. Tighten it or skip the trade.</span></div>}
          <div className="calc-row"><span className="k">Risk amount</span><span className="v">{fc(riskAmt)}</span></div>
          <div className="calc-row"><span className="k">{lot > 1 ? "Shares (lots of 100)" : "Shares"}</span>
            <span className={"v" + (capped ? " warn-v" : "")}>
              {shares.toLocaleString()}{lot > 1 ? ` (${shares / lot} lots)` : ""}{capped ? " · capped 25%" : ""}
            </span></div>
          {shares === 0 && <div className="calc-row"><span className="k" style={{ color: "var(--amber)" }}>
            Position rounds to zero — capital too small for this counter at {riskPct}% risk.</span></div>}
          <div className="calc-row"><span className="k">Position size</span>
            <span className="v">{fc(posVal)} · {Math.min(posPct, 25).toFixed(1)}% of capital</span></div>
          <div className="calc-row"><span className="k">At 2R / 3R you make</span>
            <span className="v" style={{ color: "var(--green)" }}>+{f(2 * riskAmt)} / +{f(3 * riskAmt)}</span></div>
          <div className="calc-row"><span className="k">Target prices 2R / 3R</span><span className="v">{f(t2)} / {f(t3)}</span></div>
        </>
      )}

      <button className="chip" style={{ marginTop: 10 }} onClick={() => setAdjust(!adjust)}>
        {adjust ? "hide adjust" : "adjust entry / stop / risk"}
      </button>
      {adjust && (
        <div className="calc-inputs" style={{ marginTop: 10 }}>
          <div><label>Entry</label><input type="number" step="0.01" value={entry} onChange={(x) => setEntry(x.target.value)} /></div>
          <div><label>Stop</label><input type="number" step="0.01" value={stop} onChange={(x) => setStop(x.target.value)} /></div>
          <div><label>Risk % override</label><input type="number" step="0.25" placeholder={`auto: ${autoRisk}`}
               value={riskOverride} onChange={(x) => setRiskOverride(x.target.value)} /></div>
        </div>
      )}
    </div>
  );
}
