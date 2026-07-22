import Link from "next/link";
import { candidateDetail, latestBundle, latestBacktestStatsByMarket } from "@/lib/db";
import Shell from "@/components/Shell";
import Candles from "@/components/Candles";
import Calculator from "@/components/Calculator";

export const dynamic = "force-dynamic";

const LABELS = {
  price_above_150_200: "Price > 150 & 200 MA",
  ma150_above_ma200: "150 MA > 200 MA",
  ma200_rising_1m: "200 MA rising 1m+",
  ma50_above_150_200: "50 MA > 150 & 200 MA",
  price_above_ma50: "Price > 50 MA",
  above_52w_low_30pct: "≥30% above 52w low",
  within_25pct_of_52w_high: "Within 25% of 52w high",
  rs_rank_ge_70: "RS rank ≥ 70 (funnel pool)",
};

export default async function Stock({ params }) {
  const ticker = decodeURIComponent(params.ticker);
  const [c, bundle, btByMarket] = await Promise.all([
    candidateDetail(ticker), latestBundle(), latestBacktestStatsByMarket(),
  ]);
  const regime = bundle?.run?.regime || null;

  if (!c) {
    return (
      <Shell regime={regime} asOf={bundle?.run?.run_date?.slice(0, 10)}>
        <div className="panel"><h3>{ticker}</h3>
          <div className="reasoning">Not on the current board — it either failed the screen or hasn't been scanned. <Link href="/" style={{ color: "var(--blue)" }}>Back to screener</Link></div>
        </div>
      </Shell>
    );
  }

  const ccy = "$";
  // this row is the ticker's LAST appearance — which may predate the latest
  // run. Everything on the page (price, chart, AI plan) is from that date, so
  // say it loudly instead of letting old data pass as current.
  const latestRun = bundle?.run?.run_date?.slice(0, 10);
  const rowDate = c.as_of?.slice(0, 10);
  const stale = latestRun && rowDate && rowDate < latestRun;
  const checks = c.checks || {};
  const vcp = c.vcp || {};
  const setup = c.setup || {};
  const contr = vcp.contractions_pct || [];

  // backtest-calibrated levels: this market's latest run, in R-multiples of
  // THIS counter's own risk (pivot − stop). Stop = exit when wrong; avg win =
  // where winners typically ended; expectancy = the statistical average of
  // all trades — a calibration line, NOT an exit target.
  const bt = btByMarket?.[c.market] || null;
  const bs = bt?.stats || {};
  let btLevels = [];
  if (c.pivot && c.stop && Number(c.pivot) > Number(c.stop)) {
    const piv = Number(c.pivot), stp = Number(c.stop), risk = piv - stp;
    btLevels = [
      { price: stp, label: "stop −1R", color: "var(--red)" },
      bs.expectancy_r != null && {
        price: piv + bs.expectancy_r * risk,
        label: `expectancy ${bs.expectancy_r >= 0 ? "+" : ""}${bs.expectancy_r}R`,
        color: "var(--blue)",
      },
      bs.avg_win_r != null && {
        price: piv + bs.avg_win_r * risk,
        label: `avg win +${bs.avg_win_r}R`,
        color: "var(--green)",
      },
    ].filter(Boolean);
  }

  return (
    <Shell regime={regime} asOf={c.as_of?.slice(0, 10)}>
      <div className="page-head">
        <div>
          <h1>{c.ticker} {setup.ipo ? <span className="tag ipo">IPO</span> : null}</h1>
          <div className="asof">{c.name || ""} · {c.sector || "—"} · <Link href="/" style={{ color: "var(--blue)" }}>← screener</Link></div>
        </div>
      </div>

      {stale && (
        <div className="warn" style={{ marginBottom: 14 }}>
          <b>Stale — dropped from the board</b>
          This counter last appeared on the {rowDate} scan and is NOT on the current board
          ({latestRun}). Every number below — price, chart, trade plan, AI note — is from
          {" "}{rowDate}, not today. Treat any entry/stop here as expired.
        </div>
      )}

      <div className="detail-grid">
        <div>
          <div className="panel">
            <div className="detail-head">
              <div><span className="t">{c.ticker}</span><span className="n">{c.industry || c.sector || ""}</span></div>
              <div className="p">{ccy}{Number(c.price).toFixed(2)}</div>
            </div>
            <Candles candles={c.candles} pivot={c.pivot} market={c.market} levels={btLevels}
              markers={c.patterns?.chart_markers} swings={vcp.swings} contractions={contr}
              bases={setup.base_count?.bases} />
            <div className="legend">
              <span><span className="l20">—</span> 20 MA</span>
              <span><span className="l50">—</span> 50 MA</span>
              <span><span className="l150">—</span> 150 MA</span>
              <span><span className="l200">—</span> 200 MA</span>
              {contr.length > 0 && <span className="right">Contractions: {contr.map((d) => d + "%").join(" → ")}</span>}
            </div>
            <div className="legend">
              <span style={{ color: "var(--green)" }}>▲ signal day</span>
              <span style={{ color: "var(--blue)" }}>○ pullback tag / pattern</span>
              {(vcp.swings || []).length > 0 && <span style={{ color: "var(--amber)" }}>╲ VCP contraction (depth %)</span>}
              {(setup.base_count?.bases || []).length > 0 && <span style={{ color: "var(--purple)" }}>▒ base span (start ▾ end)</span>}
            </div>
            {bt && btLevels.length > 1 && (
              <div className="legend">
                <span className="right">
                  Dashed levels calibrated from the latest {"US"} backtest
                  ({bt.label || `#${bt.id}`}, {bs.trades ?? "?"} trades) on this counter's own risk
                  (pivot − stop). Expectancy is the average of ALL trades — a reality check, not an
                  exit target; exits are the stop (wrong) or selling into strength near avg win (right).
                </span>
              </div>
            )}
          </div>

          {(setup.institutional || setup.capital_flow) && (
            <div className="panel" style={{ marginTop: 14 }}>
              <h3>Institutional sponsorship <span className="tag neutral">moomoo</span></h3>
              <div className="kv">
                {setup.institutional && (
                  <>
                    <div><span className="k">Institutions holding</span>
                      <span className="v">{setup.institutional.inst_count?.toLocaleString()}
                        {setup.institutional.inst_count_change != null && (
                          <span className={setup.institutional.inst_count_change >= 0 ? "up" : "down"}>
                            {" "}({setup.institutional.inst_count_change >= 0 ? "+" : ""}
                            {setup.institutional.inst_count_change} QoQ)
                          </span>
                        )}
                      </span></div>
                    <div><span className="k">% of float held</span>
                      <span className="v">{setup.institutional.holder_pct}%
                        {setup.institutional.holder_pct_change != null && (
                          <span className={setup.institutional.holder_pct_change >= 0 ? "up" : "down"}>
                            {" "}({setup.institutional.holder_pct_change >= 0 ? "+" : ""}
                            {setup.institutional.holder_pct_change}% QoQ)
                          </span>
                        )}
                      </span></div>
                  </>
                )}
                {setup.capital_flow && (
                  <div><span className="k">Whale flow (super+big)</span>
                    <span className={"v " + (setup.capital_flow.whale_net >= 0 ? "up" : "down")}>
                      {setup.capital_flow.whale_net >= 0 ? "+" : "−"}$
                      {Math.abs(setup.capital_flow.whale_net).toLocaleString()}
                    </span></div>
                )}
              </div>
              <div className="sub" style={{ marginTop: 8 }}>
                O&apos;Neil&apos;s &quot;I&quot; in CAN SLIM: rising institution count and float % means funds are
                <b> building</b>; falling means distribution. Whale flow is the day&apos;s net
                super/big-order money — confirmation that the sponsorship is acting today.
              </div>
            </div>
          )}

          {(c.ai_note?.assessment?.length > 0 || c.reasoning_sections?.length > 0 || c.reasoning) && (
            <div className="panel" style={{ marginTop: 14 }}>
              <h3>
                Why it's on the board
                {c.ai_note?.assessment?.length > 0
                  ? <span className="tag us">AI-written · refreshed nightly</span>
                  : <span className="tag neutral">mechanical</span>}
              </h3>
              {c.ai_note?.assessment?.length > 0 ? (
                c.ai_note.assessment.map((sec, si) => (
                  <div key={si} className={`rsec ${sec.tone === "info" ? "" : sec.tone || ""}`}>
                    <div className="rsec-t">{sec.title}</div>
                    {(sec.lines || []).map((line, i) => (
                      <div key={i} className="rsec-l">{line}</div>
                    ))}
                  </div>
                ))
              ) : c.reasoning_sections?.length > 0 ? (
                c.reasoning_sections.map((sec) => (
                  <div key={sec.key} className={`rsec ${sec.tone || ""}`}>
                    <div className="rsec-t">{sec.title}</div>
                    {(sec.lines || []).map((line, i) => (
                      <div key={i} className="rsec-l">{line}</div>
                    ))}
                  </div>
                ))
              ) : (
                <div className="reasoning">{c.reasoning}</div>
              )}
            </div>
          )}

          {c.ai_note && (c.ai_note.plan || c.ai_note.summary || c.ai_note.note) && (
            <div className="panel" style={{ marginTop: 14 }}>
              <h3>
                AI trade plan{" "}
                {c.ai_note.verdict && (
                  <span className={`tag ${
                    { "buy-at-pivot": "good", "early-entry": "us", wait: "pivot", avoid: "bad" }[c.ai_note.verdict] || "neutral"
                  }`}>{c.ai_note.verdict}</span>
                )}{" "}
                <span className={`tag ${c.ai_note.risk === "high" ? "bad" : c.ai_note.risk === "medium" ? "pivot" : "neutral"}`}>
                  news risk: {c.ai_note.risk || "unknown"}
                </span>{" "}
                <span className="tag neutral">AI — not a signal</span>
              </h3>
              {(c.ai_note.summary || c.ai_note.note) && (
                <div className="reasoning" style={{ marginBottom: 10 }}>{c.ai_note.summary || c.ai_note.note}</div>
              )}
              {c.ai_note.plan && ["entry", "stop", "targets", "invalidation"].map((k) =>
                c.ai_note.plan[k] ? (
                  <div className="plan-row" key={k}>
                    <span className="k">{k}</span>
                    <span className="v">{c.ai_note.plan[k]}</span>
                  </div>
                ) : null
              )}
              {c.ai_note.generated_at && (
                <div className="sub" style={{ marginTop: 8 }}>
                  Generated {String(c.ai_note.generated_at).slice(0, 16).replace("T", " ")} UTC
                  from the {rowDate} scan&apos;s computed data.
                </div>
              )}
              {(c.ai_note.news || []).length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <div className="rsec-t">Recent news</div>
                  {c.ai_note.news.map((n, i) => (
                    <div className="news-item" key={i}>
                      <span className="d">{n.date}</span> <b>{n.headline}</b>
                      {n.impact && <div className="impact">{n.impact}</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="panel">
            <h3>Trend Template {setup.ipo ? `(young stock — ${setup.rules_total} rules)` : ""}</h3>
            {Object.entries(checks).map(([k, v]) => {
              const nums = Object.entries(v)
                .filter(([kk, vv]) => kk !== "pass" && kk !== "note" && vv != null && typeof vv !== "object")
                .map(([kk, vv]) => `${kk.replace(/_/g, " ")}: ${vv}`)
                .join(" · ");
              return (
                <div key={k} className="check-row" style={{ alignItems: "flex-start" }}>
                  <span className={"dot" + (v.pass ? " ok" : "")} style={{ marginTop: 5 }} />
                  <span>
                    <span className={"lbl" + (v.pass ? " pass" : "")}>{LABELS[k] || k}</span>
                    {nums && <span style={{ display: "block", fontSize: 10.5, color: "var(--faint)", fontFamily: "var(--mono)" }}>{nums}</span>}
                  </span>
                </div>
              );
            })}
            <div className={"vcp-note" + (vcp.vcp ? " on" : "")}>
              {vcp.vcp
                ? `✓ VCP: ${contr.length} contractions, volume dry-up ${vcp.volume_dry_up ? "confirmed" : "pending"}`
                : contr.length >= 2 ? "Base building — contractions found, dry-up/pivot pending" : "No valid VCP in the current base"}
            </div>
            {(setup.warnings || []).map((w, i) => (
              <div key={i} className="warn"><b>{w.title}</b>{w.do}</div>
            ))}
            {c.earnings?.high_risk && (
              <div className="warn"><b>Earnings {c.earnings.date}</b>Within a week — a breakout can gap straight through the stop.</div>
            )}
          </div>

          {c.fundamentals && (
            <div className="panel">
              <h3>
                Quarterly fundamentals
                {c.fundamentals.grade && (
                  <span className={`tag ${{ A: "good", B: "good", C: "neutral", D: "bad", E: "bad" }[c.fundamentals.grade]}`}>
                    grade {c.fundamentals.grade}
                  </span>
                )}
                {(c.fundamentals.eps_accelerating || c.fundamentals.accelerating)
                  ? <span className="tag good">{c.fundamentals.eps_accelerating ? "EPS" : "NI"} accelerating</span>
                  : c.fundamentals.ni_yoy_pct != null && c.fundamentals.ni_yoy_prev_pct != null
                    ? <span className="tag neutral">not accelerating</span> : null}
              </h3>
              {[["Revenue YoY (latest q)", c.fundamentals.rev_yoy_pct, "%"],
                ["Revenue YoY (prior q)", c.fundamentals.rev_yoy_prev_pct, "%"],
                ["EPS YoY (latest q)", c.fundamentals.eps_yoy_pct, "%"],
                ["EPS YoY (prior q)", c.fundamentals.eps_yoy_prev_pct, "%"],
                ["Net income YoY (latest q)", c.fundamentals.ni_yoy_pct, "%"],
                ["Net income YoY (prior q)", c.fundamentals.ni_yoy_prev_pct, "%"],
                ["Net margin vs year ago", c.fundamentals.margin_delta_pp, "pp"],
                ["Last EPS surprise", c.fundamentals.surprise_pct, "%"]].map(([k, v, u]) => (
                <div className="calc-row" key={k}>
                  <span className="k">{k}</span>
                  <span className="v" style={{ color: v == null ? "var(--faint)" : v >= 0 ? "var(--green)" : "var(--red)" }}>
                    {v == null ? "n/a" : `${v > 0 ? "+" : ""}${v}${u}`}
                  </span>
                </div>
              ))}
              {[["ROE", c.fundamentals.roe_pct, "%", 17],
                ["Debt / equity", c.fundamentals.debt_to_equity, "", null]].map(([k, v, u, good]) => (
                <div className="calc-row" key={k}>
                  <span className="k">{k}</span>
                  <span className="v" style={{ color: v == null ? "var(--faint)" : good != null && v >= good ? "var(--green)" : "var(--ink)" }}>
                    {v == null ? "n/a" : `${v}${u}`}
                  </span>
                </div>
              ))}
              <div className="sub" style={{ marginTop: 6 }}>
                Quarter ended {c.fundamentals.quarter_end} · computed from
                {" filings"};
                "n/a" usually means an unprofitable base quarter or missing coverage. Grade is a
                mechanical scorecard (EPS 25%+ · revenue 20%+ · accelerating · margin expanding ·
                ROE 17%+), graded only on the boxes with data.
              </div>
            </div>
          )}

          <div className="panel">
            <h3>Position sizing</h3>
            <Calculator entry={c.pivot || c.price} stop={c.stop} market={c.market}
                        exposure={regime?.[c.market]?.exposure}
                        light={regime?.[c.market]?.light} />
          </div>
        </div>
      </div>
    </Shell>
  );
}
