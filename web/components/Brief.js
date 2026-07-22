const TONE = {
  "risk-on": ["good", "risk-on"],
  neutral: ["neutral", "neutral"],
  "risk-off": ["bad", "risk-off"],
};
const SECTOR_IMPACT = { tailwind: ["good", "▲"], headwind: ["bad", "▼"], watch: ["pivot", "●"] };
const COUNTER_IMPACT = { positive: ["good", "▲"], negative: ["bad", "▼"], watch: ["pivot", "●"] };

const tkr = (t) => (
  <a key={t} className="tkr" href={`/stock/${encodeURIComponent(t)}`}>{t.replace(".KL", "")}</a>
);

export default function Brief({ brief }) {
  const markets = ["US"].filter(
    (m) => brief?.[m] && (brief[m].headline || brief[m].counters?.length || brief[m].bullets?.length)
  );
  if (!markets.length) return null;
  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <h3>
        AI morning brief <span className="tag neutral">AI commentary — not signals</span>
      </h3>
      <div className={"brief-grid" + (markets.length > 1 ? "" : " one")}>
        {markets.map((m) => {
          const b = brief[m];
          const [cls, label] = TONE[b.tone] || TONE.neutral;
          return (
            <div key={m}>
              <div className="brief-mkt">
                <b>US</b> <span className={`tag ${cls}`}>{label}</span>
              </div>
              {b.headline && <div className="brief-headline">{b.headline}</div>}
              {b.action && <div className="brief-action">▶ {b.action}</div>}

              {(b.sectors || []).length > 0 && (
                <div className="brief-block">
                  <div className="rsec-t">Sectors affected</div>
                  {b.sectors.map((s, i) => {
                    const [icls, icon] = SECTOR_IMPACT[s.impact] || SECTOR_IMPACT.watch;
                    return (
                      <div className="brief-sector" key={i}>
                        <span className={`imp ${icls}`}>{icon}</span>
                        <span className="s-name">{s.sector}</span>
                        <span className="s-why">
                          {s.why}
                          {(s.counters || []).length > 0 && <> · {s.counters.map(tkr)}</>}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}

              {(b.counters || []).length > 0 && (
                <div className="brief-block">
                  <div className="rsec-t">Counters in the news</div>
                  {b.counters.map((n, i) => {
                    const [icls, icon] = COUNTER_IMPACT[n.impact] || COUNTER_IMPACT.watch;
                    return (
                      <div className="brief-counter" key={i}>
                        <span className={`imp ${icls}`}>{icon}</span>
                        {tkr(n.ticker)}
                        <span className="s-why">{n.why}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* pre-v1.7 briefs stored plain bullets — keep them readable */}
              {!b.counters?.length && !b.sectors?.length && (b.bullets || []).length > 0 && (
                <ul className="brief-bullets">
                  {b.bullets.map((line, i) => <li key={i}>{line}</li>)}
                </ul>
              )}
            </div>
          );
        })}
      </div>
      {brief.generated_at && (
        <div className="sub" style={{ marginTop: 10 }}>
          Generated {String(brief.generated_at).slice(0, 16).replace("T", " ")} UTC · interprets computed
          data + headlines only; never feeds back into signals or sizing.
        </div>
      )}
    </div>
  );
}
