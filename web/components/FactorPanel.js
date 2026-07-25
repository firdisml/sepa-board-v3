/* Do the board's scores predict? Weekly decile tables from scanner/factors.py.
   The one rule that matters here: a factor whose buckets do NOT slope
   monotonically gets called out, not smoothed over — a score that doesn't
   predict is worse than no score, because it looks like information. */

const FACTOR_LABEL = {
  quality: "Quality score", rs_rank: "RS rank",
  grade: "Fundamentals grade", anticipation: "Anticipation score",
};

const BUCKET_ORDER = ["D1", "D2", "D3", "D4", "D5",
                      "D6", "D7", "D8", "D9", "D10", "E", "D", "C", "B", "A"];

export default function FactorPanel({ rows }) {
  if (!rows?.length) return null;

  const byFactor = {};
  for (const r of rows) (byFactor[r.factor] = byFactor[r.factor] || []).push(r);

  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <h3>Factor validation — do the scores predict?</h3>
      <div className="reasoning" style={{ marginBottom: 10 }}>
        Every historical board row, bucketed by score, measured on its
        20-session forward return. Low bucket → high bucket should slope up;
        where it doesn&apos;t, the score isn&apos;t earning its place on the board.
      </div>
      {Object.entries(byFactor).map(([factor, list]) => {
        const sorted = [...list].sort(
          (a, b) => BUCKET_ORDER.indexOf(a.bucket) - BUCKET_ORDER.indexOf(b.bucket));
        const monotone = sorted[0]?.monotone;
        return (
          <div key={factor} style={{ marginBottom: 12 }}>
            <div className="rsec-t">
              {FACTOR_LABEL[factor] || factor}{" "}
              {monotone
                ? <span className="tag good">predictive slope</span>
                : <span className="tag bad">NOT monotone — treat with suspicion</span>}
            </div>
            <div className="bt-wrap">
              <table className="bt">
                <thead><tr>
                  <th className="ta-l">Bucket (low → high)</th>
                  <th className="ta-r">N</th>
                  <th className="ta-r">Fwd 20d mean</th>
                  <th className="ta-r">Median</th>
                  <th className="ta-r">Win%</th>
                </tr></thead>
                <tbody>
                  {sorted.map((r) => (
                    <tr key={r.bucket}>
                      <td className="ta-l sym">{r.bucket}</td>
                      <td className="ta-r num">{r.n}</td>
                      <td className={"ta-r num" + (r.fwd20_mean > 0 ? " pos" : r.fwd20_mean < 0 ? " neg" : "")}>
                        {Number(r.fwd20_mean).toFixed(2)}%
                      </td>
                      <td className="ta-r num">{Number(r.fwd20_median).toFixed(2)}%</td>
                      <td className="ta-r num">{Number(r.win_rate).toFixed(0)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      <div className="legend">
        <span className="right">
          Recomputed Sundays by the weekly review; rows too young to have 20
          sessions of follow-through are skipped, never graded early.
        </span>
      </div>
    </div>
  );
}
