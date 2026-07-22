import { NextResponse } from "next/server";

export async function POST(req) {
  const { password } = await req.json();
  if (password !== process.env.DASHBOARD_PASSWORD) {
    return NextResponse.json({ ok: false }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set("sb_auth", password, {
    httpOnly: true, sameSite: "lax", secure: true, maxAge: 60 * 60 * 24 * 90, path: "/",
  });
  return res;
}
