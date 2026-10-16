"use client";

import { useState } from "react";

export default function Login() {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const res = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }) });
    setBusy(false);
    if (res.ok) window.location.href = "/";
    else setErr("Senha incorreta");
  };
  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={submit}>
        <h1><span>BingX</span> Quant Bot</h1>
        <div className="field"><label>Senha do painel</label>
          <input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        {err && <div className="alert error">{err}</div>}
        <button className="btn primary" disabled={busy || !password}>{busy ? "Entrando..." : "Entrar"}</button>
      </form>
    </div>
  );
}
