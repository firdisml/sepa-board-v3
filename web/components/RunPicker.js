"use client";

export default function RunPicker({ runs, selected }) {
  return (
    <select
      className="select"
      value={selected ?? ""}
      onChange={(e) => (window.location.href = `/backtest?id=${e.target.value}`)}
    >
      {runs.map((r) => (
        <option key={r.id} value={r.id}>
          {(r.label || `run #${r.id}`) + (r.params?.market && !(r.label || "").includes(r.params.market) ? ` [${r.params.market}]` : "")} · {String(r.created_at).slice(0, 10)}
        </option>
      ))}
    </select>
  );
}
