"use client";
import { useState } from "react";
import { money, price as fmtPrice } from "@/lib/format";

/* Right rail — the key-stats block moomoo puts beside the chart, then tabs.
   Everything here is a number the engine computed or a document the counter
   filed; nothing is inferred at render time. */

function Stat({ k, v, tone }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className={"v" + (tone ? ` ${tone}` : "")}>{v ?? "—"}</span>
    </div>
  );
}

export default function SidePanel({ c }) {
  const [tab, setTab] = useState("Plan");
  if (!c) return <div className="side"><div className="side-empty">Pick a counter.</div></div>;

  const m = c.market;
  const setup = c.setup || {};
  const street = setup.street || {};
  const anns = street.announcements || [];
  const news = c.news || [];
  const sh = street.shareholding || {};
  const f = c.fundamentals || {};
  const piv = Number(c.pivot), stp = Number(c.stop);
  const riskPct = piv && stp ? ((piv - stp) / piv) * 100 : null;

  const TABS = ["Plan", "News", "Filings", "Fundamentals"];

  return (
    <div className="side">
      <div className="side-head">
        <div className="side-sym">{c.ticker.replace(".KL", "")}</div>
        <div className="side-name">{c.name || ""}</div>
        <div className="side-px">{money(c.price, m)}</div>
      </div>

      <div className="side-stats">
        <Stat k="Pivot" v={c.pivot ? fmtPrice(c.pivot, m) : null} tone="y" />
        <Stat k="Stop" v={c.stop ? fmtPrice(c.stop, m) : null} tone="r" />
        <Stat k="Target 2R" v={c.target_2r ? fmtPrice(c.target_2r, m) : null} tone="g" />
        <Stat k="Target 3R" v={c.target_3r ? fmtPrice(c.target_3r, m) : null} tone="g" />
        <Stat k="RS rank" v={c.rs_rank} />
        <Stat k="Group RS" v={c.group_rs} />
        <Stat k="Quality" v={c.quality} />
        <Stat k="ADR" v={c.adr_pct != null ? `${c.adr_pct}%` : null} />
        <Stat k="Risk" v={riskPct != null ? `${riskPct.toFixed(1)}%` : null} />
        <Stat k="Grade" v={f.grade} tone={f.grade === "A" || f.grade === "B" ? "g" : ""} />
      </div>

      <div className="side-tabs">
        {TABS.map((t) => (
          <button key={t} className={"stab" + (t === tab ? " on" : "")}
                  onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      <div className="side-body">
        {tab === "Plan" && (
          <>
            {c.ai_note?.verdict && (
              <div className={"verdict " + (c.ai_note.verdict === "avoid" ? "bad" : "")}>
                {c.ai_note.verdict}
                <span className="badge-ai">AI</span>
              </div>
            )}
            {c.ai_note?.summary && <p className="side-p">{c.ai_note.summary}</p>}
            {c.ai_note?.plan?.entry && (
              <><div className="side-lbl">Entry</div><p className="side-p">{c.ai_note.plan.entry}</p></>
            )}
            {c.ai_note?.plan?.stop && (
              <><div className="side-lbl">Stop</div><p className="side-p">{c.ai_note.plan.stop}</p></>
            )}
            {!c.ai_note && <p className="side-p dim">No AI note for this counter.</p>}
            {(setup.warnings || []).length > 0 && (
              <>
                <div className="side-lbl">Warnings</div>
                {setup.warnings.map((w, i) => (
                  <p key={i} className="side-p warn-p">{typeof w === "string" ? w : w.text || w.label}</p>
                ))}
              </>
            )}
          </>
        )}

        {tab === "News" && (
          news.length === 0
            ? <p className="side-p dim">No press coverage — institutionally undiscovered.
                Filings still apply.</p>
            : news.map((n, i) => (
                <a key={i} className="feed" href={n.url} target="_blank" rel="noreferrer">
                  <span className="feed-d">{(n.date || "").slice(0, 10)}</span>
                  <span className="feed-t">{n.title}</span>
                  {n.source && <span className="feed-s">{n.source}</span>}
                </a>
              ))
        )}

        {tab === "Filings" && (
          <>
            {sh.net_shares != null && (
              <div className={"sponsor " + (sh.net_shares > 0 ? "g" : sh.net_shares < 0 ? "r" : "")}>
                Substantial shareholders {sh.net_shares > 0 ? "accumulated" : "distributed"}{" "}
                <b>{Math.abs(sh.net_shares).toLocaleString()}</b> shares over {sh.window_days}d
              </div>
            )}
            {anns.length === 0
              ? <p className="side-p dim">No announcements parsed.</p>
              : anns.map((a, i) => (
                  <a key={i} className="feed" href={a.url} target="_blank" rel="noreferrer">
                    <span className="feed-d">{(a.date || "").slice(0, 10)}</span>
                    <span className="feed-t">{a.title}</span>
                    <span className={"tag " + (
                      a.category === "dilution" || a.category === "uma" ? "bad"
                        : a.category === "contract" || a.category === "results" ? "good" : "neutral"
                    )}>{a.category}</span>
                  </a>
                ))}
          </>
        )}

        {tab === "Fundamentals" && (
          f.grade || f.rev_yoy_pct != null ? (
            <div className="side-stats col">
              <Stat k="Grade" v={f.grade} />
              <Stat k="Revenue YoY" v={f.rev_yoy_pct != null ? `${f.rev_yoy_pct}%` : null}
                    tone={f.rev_yoy_pct > 0 ? "g" : "r"} />
              <Stat k="Net income YoY" v={f.ni_yoy_pct != null ? `${f.ni_yoy_pct}%` : null}
                    tone={f.ni_yoy_pct > 0 ? "g" : "r"} />
              <Stat k="EPS YoY" v={f.eps_yoy_pct != null ? `${f.eps_yoy_pct}%` : null}
                    tone={f.eps_yoy_pct > 0 ? "g" : "r"} />
              <Stat k="Accelerating" v={f.accelerating == null ? null : f.accelerating ? "yes" : "no"}
                    tone={f.accelerating ? "g" : ""} />
              <Stat k="Net margin" v={f.margin_pct != null ? `${f.margin_pct}%` : null} />
              <Stat k="ROE (ann.)" v={f.roe_pct != null ? `${f.roe_pct}%` : null} />
              <Stat k="Quarter end" v={f.quarter_end} />
            </div>
          ) : <p className="side-p dim">No parseable financials — grade withheld rather than guessed.</p>
        )}
      </div>
    </div>
  );
}
