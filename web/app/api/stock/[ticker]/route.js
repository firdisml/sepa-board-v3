import { NextResponse } from "next/server";
import { candidateDetail } from "@/lib/db";

// The one-page workbench (app/page.js + components/Board.js) fetches per-
// ticker detail on click instead of navigating to a page: the list bundle
// (lib/db.js latestBundle) deliberately omits candles/ai_note/patterns to
// keep the board-wide query light, so the heavy `c.*` row is a separate,
// on-demand fetch.
export const dynamic = "force-dynamic";

export async function GET(_req, { params }) {
  const ticker = decodeURIComponent(params.ticker);
  const c = await candidateDetail(ticker);
  if (!c) return NextResponse.json({ error: "not found" }, { status: 404 });
  return NextResponse.json(c);
}
