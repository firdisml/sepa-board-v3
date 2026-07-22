export default function LineChart({ points, color = "var(--blue)", height = 180, fmt = (v) => v }) {
  // points: [{t, v}] — renders a simple line with a zero/start baseline
  if (!points || points.length < 2) return <div className="reasoning">Not enough data to chart yet.</div>;
  const W = 760, H = height, PAD = 6;
  const vals = points.map((p) => p.v);
  const hi = Math.max(...vals), lo = Math.min(...vals);
  const span = hi - lo || 1;
  const x = (i) => (i / (points.length - 1)) * (W - PAD * 2) + PAD;
  const y = (v) => H - PAD - ((v - lo) / span) * (H - PAD * 2);
  const path = points.map((p, i) => `${x(i)},${y(p.v)}`).join(" ");
  const startY = y(points[0].v);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }} role="img">
      <line x1={PAD} x2={W - PAD} y1={startY} y2={startY} stroke="var(--line)" strokeDasharray="4 4" />
      <polyline points={path} fill="none" stroke={color} strokeWidth="1.8" />
      <text x={W - PAD} y={y(vals[vals.length - 1]) - 6} fill={color} fontSize="11" textAnchor="end" fontFamily="var(--mono)">
        {fmt(vals[vals.length - 1])}
      </text>
    </svg>
  );
}
