"use client";

export default function Candles({ candles, pivot, market, levels, markers, swings, contractions, bases }) {
  const data = (candles || []).slice(-120);
  if (data.length < 10) return <div className="reasoning">No candle data stored for this pick.</div>;
  const W = 760, H = 300, VB = 54;
  const n = data.length;
  const dataHi = Math.max(...data.map((b) => b.h));
  const dataLo = Math.min(...data.map((b) => b.l));
  // extra horizontal levels (stop / expectancy / avg win); ones far outside
  // the candle range are dropped rather than squishing the whole chart
  const lvls = (levels || []).filter((l) => l && l.price != null && isFinite(l.price)
    && l.price <= dataHi * 1.15 && l.price >= dataLo * 0.9);
  const hi = Math.max(dataHi, pivot || -Infinity, ...lvls.map((l) => l.price)) * 1.01;
  const lo = Math.min(dataLo, ...lvls.map((l) => l.price)) * 0.99;
  const y = (v) => ((hi - v) / (hi - lo)) * (H - VB - 8) + 4;
  const bw = W / n;
  const vmax = Math.max(...data.map((b) => b.v || 0), 1);
  const ccy = "$";

  // date -> bar index, for everything that anchors to a day
  const xi = new Map(data.map((b, i) => [b.t, i]));
  const xc = (i) => i * bw + bw / 2;

  const maLine = (key, color) => {
    const pts = data.map((b, i) => (b[key] != null && b[key] >= lo && b[key] <= hi ? `${xc(i)},${y(b[key])}` : null)).filter(Boolean);
    if (pts.length < 2) return null;
    return <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.4" opacity="0.85" />;
  };

  // ---- base spans (stage analysis): shade the exact stretch that was counted,
  // with small triangles pointing at where it starts and ends
  const baseSpans = (bases || []).map((b) => {
    const endIn = b.end != null && xi.has(b.end);
    const startIn = xi.has(b.start);
    // whole base before the window -> nothing to draw
    if (!startIn && !endIn && b.end != null) return null;
    const x0 = startIn ? xi.get(b.start) : 0;
    const x1 = b.end == null ? n - 1 : (endIn ? xi.get(b.end) : n - 1);
    if (x1 <= x0) return null;
    return { n: b.n, x0, x1, startIn, endIn, forming: b.end == null };
  }).filter(Boolean);

  // ---- VCP contraction swings: dashed high->low lines with the depth label
  const swingLines = (swings || []).map((s, i) => {
    const [h0, l0] = s;
    if (!xi.has(h0.t) || !xi.has(l0.t)) return null;
    return { x0: xi.get(h0.t), p0: h0.p, x1: xi.get(l0.t), p1: l0.p,
             depth: (contractions || [])[i] };
  }).filter(Boolean);

  // ---- dated event markers, stacked per day so they never overlap
  const byDay = new Map();
  for (const m of markers || []) {
    if (!m || !xi.has(m.t)) continue;
    if (!byDay.has(m.t)) byDay.set(m.t, []);
    byDay.get(m.t).push(m);
  }
  const markerEls = [];
  for (const [t, ms] of byDay) {
    const i = xi.get(t);
    const b = data[i];
    let below = 0, above = 0;
    for (const m of ms.slice(0, 5)) {
      const up = m.position !== "aboveBar";
      const my = up ? y(b.l) + 7 + below * 12 : y(b.h) - 7 - above * 12;
      if (up) below += 1; else above += 1;
      const col = m.shape === "arrowUp" ? "var(--green)" : m.shape === "arrowDown" ? "var(--red)" : "var(--blue)";
      const nearRight = i > n * 0.82;
      markerEls.push(
        <g key={t + m.text + my}>
          {m.shape === "circle"
            ? <circle cx={xc(i)} cy={my} r="2.6" fill="none" stroke={col} strokeWidth="1.3" />
            : <path d={m.shape === "arrowUp"
                ? `M ${xc(i)} ${my - 3.5} l 4 6.5 l -8 0 z`
                : `M ${xc(i)} ${my + 3.5} l 4 -6.5 l -8 0 z`} fill={col} />}
          <text x={nearRight ? xc(i) - 6 : xc(i) + 6} y={my + 3} fill={col} fontSize="8.5"
            textAnchor={nearRight ? "end" : "start"} fontFamily="var(--mono)">{m.text}</text>
        </g>
      );
    }
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }} role="img" aria-label="Daily candlestick chart">
      {baseSpans.map((s) => (
        <g key={"base" + s.n}>
          <rect x={s.x0 * bw} y={4} width={(s.x1 - s.x0 + 1) * bw} height={H - VB - 8}
            fill="var(--purple)" opacity="0.07" />
          {s.startIn && <path d={`M ${xc(s.x0)} 12 l 4 -7 l -8 0 z`} fill="var(--purple)" opacity="0.9" />}
          {(s.endIn || s.forming) && <path d={`M ${xc(s.x1)} 12 l 4 -7 l -8 0 z`} fill="var(--purple)" opacity="0.9" />}
          <text x={s.x0 * bw + 4} y={16} fill="var(--purple)" fontSize="9" fontFamily="var(--mono)" opacity="0.95">
            base {s.n}{s.forming ? " (forming)" : ""}
          </text>
        </g>
      ))}
      {swingLines.map((s, i) => (
        <g key={"sw" + i}>
          <line x1={xc(s.x0)} y1={y(s.p0)} x2={xc(s.x1)} y2={y(s.p1)}
            stroke="var(--amber)" strokeWidth="1.2" strokeDasharray="3 3" opacity="0.8" />
          {s.depth != null && (
            <text x={xc(s.x1) + 4} y={y(s.p1) + 9} fill="var(--amber)" fontSize="8.5"
              textAnchor={s.x1 > n * 0.85 ? "end" : "start"} fontFamily="var(--mono)" opacity="0.9">
              −{s.depth}%
            </text>
          )}
        </g>
      ))}
      {pivot && pivot <= hi && pivot >= lo && (
        <>
          <line x1="0" x2={W} y1={y(pivot)} y2={y(pivot)} stroke="var(--amber)" strokeDasharray="5 4" strokeWidth="1.2" />
          <text x={W - 4} y={y(pivot) - 5} fill="var(--amber)" fontSize="10" textAnchor="end" fontFamily="var(--mono)">
            pivot {ccy}{Number(pivot).toFixed(2)}
          </text>
        </>
      )}
      {lvls.map((l, i) => (
        <g key={"lvl" + i}>
          <line x1="0" x2={W} y1={y(l.price)} y2={y(l.price)} stroke={l.color} strokeDasharray="2 4" strokeWidth="1" opacity="0.9" />
          <text x={4} y={y(l.price) - 4} fill={l.color} fontSize="9.5" fontFamily="var(--mono)">
            {l.label} {ccy}{Number(l.price).toFixed(2)}
          </text>
        </g>
      ))}
      {data.map((b, i) => {
        const up = b.c >= b.o;
        const col = up ? "var(--green)" : "var(--red)";
        const hv = b.v50 && b.v > 1.5 * b.v50;
        return (
          <g key={i}>
            <line x1={xc(i)} x2={xc(i)} y1={y(b.h)} y2={y(b.l)} stroke={col} strokeWidth="1" />
            <rect x={i * bw + bw * 0.2} width={bw * 0.6} y={y(Math.max(b.o, b.c))}
              height={Math.max(1.5, Math.abs(y(b.o) - y(b.c)))} fill={col} rx="1" />
            <rect x={i * bw + bw * 0.2} width={bw * 0.6} y={H - ((b.v || 0) / vmax) * (VB - 8)}
              height={((b.v || 0) / vmax) * (VB - 8)} fill={hv ? "var(--amber)" : "var(--faint)"} opacity={hv ? 0.8 : 0.45} rx="1" />
          </g>
        );
      })}
      {maLine("m20", "#3dd6c3")}
      {maLine("m50", "var(--blue)")}
      {maLine("m150", "var(--amber)")}
      {maLine("m200", "var(--purple)")}
      {markerEls}
    </svg>
  );
}
