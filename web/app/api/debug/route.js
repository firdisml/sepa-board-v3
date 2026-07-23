import { NextResponse } from "next/server";
import { db } from "@/lib/db";

// TEMPORARY diagnostic — remove after use. Local pooler access was blocked so
// this reads ground truth through Vercel's healthy connection.
export const dynamic = "force-dynamic";

export async function GET() {
  const sql = db();
  const runs = await sql`SELECT id, run_date, created_at FROM scan_runs ORDER BY created_at DESC LIMIT 3`;
  const latest = runs[0]?.id;
  const cand = await sql`
    SELECT count(*)::int AS n,
           count(*) FILTER (WHERE fundamentals IS NOT NULL)::int AS non_null,
           count(*) FILTER (WHERE fundamentals->>'grade' IS NOT NULL)::int AS graded
    FROM candidates WHERE run_id = ${latest}`;
  const cache = await sql`
    SELECT count(*)::int AS n,
           count(*) FILTER (WHERE data->>'grade' IS NOT NULL)::int AS graded,
           max(updated_at) AS newest
    FROM bursa_fundamentals`;
  const sample = await sql`
    SELECT ticker, fundamentals FROM candidates
    WHERE run_id = ${latest} ORDER BY rs_rank DESC LIMIT 2`;
  const cacheSample = await sql`
    SELECT ticker, data FROM bursa_fundamentals ORDER BY updated_at DESC LIMIT 2`;
  return NextResponse.json({ runs, candidates: cand[0], cache: cache[0], sample, cacheSample });
}
