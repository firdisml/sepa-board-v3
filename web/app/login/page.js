"use client";
import { useState } from "react";

export default function Login() {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState(false);
  async function submit(e) {
    e.preventDefault();
    const r = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    if (r.ok) window.location.href = "/";
    else setErr(true);
  }
  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>SEPA <span style={{ color: "var(--blue)" }}>Board</span></h1>
        <p>Minervini screener — Bursa Malaysia</p>
        <input type="password" placeholder="Password" value={pw}
          onChange={(e) => { setPw(e.target.value); setErr(false); }} autoFocus />
        <button type="submit">Sign in</button>
        {err && <div className="login-err">Wrong password — check DASHBOARD_PASSWORD.</div>}
      </form>
    </div>
  );
}
