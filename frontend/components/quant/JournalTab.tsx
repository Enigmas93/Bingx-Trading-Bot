"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import { EXIT_LABEL, REGIME_SHORT, StrategiesResponse, TIMEFRAMES, TradeBrief, cls, fmtDuration, fmtR } from "@/lib/quant";
import { Dir, Empty, Help, Level, Regime } from "./ui";
import TradeDetail from "./TradeDetail";

export default function JournalTab() {
  const [rows, setRows] = useState<TradeBrief[]>([]);
  const [strats, setStrats] = useState<string[]>([]);
  const [f, setF] = useState({ q: "", status: "", strategy: "", direction: "", timeframe: "", regime: "", exit_reason: "" });
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(() => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    api<TradeBrief[]>(`/quant/journal${qs ? `?${qs}` : ""}`).then(setRows);
  }, [f]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api<StrategiesResponse>("/quant/strategies").then((s) => setStrats(s.strategies.filter((x) => x.role === "signal").map((x) => x.strategy_id)));
  }, []);

  const closed = rows.filter((r) => r.status === "CLOSED");
  const total = closed.reduce((a, r) => a + (r.pnl ?? 0), 0);
  const totalR = closed.reduce((a, r) => a + (r.pnl_r ?? 0), 0);

  const sel = (key: keyof typeof f, opts: [string, string][], label: string) => (
    <div className="field"><label>{label}</label>
      <select value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })}>
        <option value="">Todos</option>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select></div>
  );

  return (
    <>
      <Help>Diário completo das operações DEMO. Nada é apagado. Cada trade guarda as estratégias (primária e de apoio),
        o score, o regime, o motivo de entrada e de saída, o PnL real e um snapshot imutável das features na entrada.</Help>
      <div className="form-grid filters">
        <div className="field"><label>Busca (par, ID, estratégia, motivo)</label>
          <input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="ex.: ETH, BREAKOUT" /></div>
        {sel("status", [["OPEN", "Aberto"], ["CLOSED", "Fechado"], ["FAILED", "Falhou"]], "Status")}
        {sel("strategy", strats.map((s) => [s, s]), "Estratégia (primária ou apoio)")}
        {sel("direction", [["LONG", "LONG"], ["SHORT", "SHORT"]], "Direção")}
        {sel("timeframe", TIMEFRAMES.map((t) => [t, t]), "Timeframe")}
        {sel("regime", Object.entries(REGIME_SHORT), "Regime BTC na entrada")}
        {sel("exit_reason", Object.entries(EXIT_LABEL), "Motivo de saída")}
      </div>
      <div className="row muted small" style={{ margin: "8px 0" }}>
        {rows.length} trades · {closed.length} fechados · PnL <span className={cls(total)}>{fmt(total, 4)}</span> · <span className={cls(totalR)}>{fmtR(totalR)}</span>
      </div>
      {rows.length === 0 ? <Empty>Nenhum trade encontrado.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Entrada</th><th>TF</th><th>Par</th><th>Direção</th><th>Status</th><th>Primária</th><th>Apoio</th><th>Confluência</th><th>Score</th><th>Regime</th><th>Saída</th><th>R</th><th>PnL</th><th>MFE/MAE</th><th>Duração</th></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => setOpen(t.id)}>
                  <td>{fmtDate(t.entry_time)}</td><td><span className="badge tf">{t.timeframe}</span></td><td><b>{t.symbol}</b></td><td><Dir d={t.direction} /></td>
                  <td><span className={`badge ${t.status === "OPEN" ? "blue" : t.status === "FAILED" ? "red" : ""}`}>{t.status}</span></td>
                  <td>{t.strategy_primary}</td><td>{t.strategies_supporting.join(", ") || "-"}</td>
                  <td><Level l={t.confluence_level} /></td><td>{fmt(t.final_score, 1)}</td><td><Regime r={t.btc_regime} /></td>
                  <td>{t.exit_reason ? EXIT_LABEL[t.exit_reason] ?? t.exit_reason : "-"}</td>
                  <td className={cls(t.pnl_r)}>{fmtR(t.pnl_r)}</td>
                  <td className={cls(t.pnl)}>{fmt(t.pnl, 4)}{t.status === "CLOSED" && !t.pnl_confirmed && " *"}</td>
                  <td>{fmtR(t.mfe_r, 1)} / {fmtR(t.mae_r, 1)}</td><td>{fmtDuration(t.duration_minutes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open !== null && <TradeDetail id={open} onClose={() => setOpen(null)} onChanged={load} />}
    </>
  );
}
