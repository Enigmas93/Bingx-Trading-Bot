"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import { PerfRow, REGIME_SHORT, TIMEFRAMES, TF_LABEL, cls, fmtDuration, fmtR, tfOf } from "@/lib/quant";
import { Empty, Help } from "./ui";

type Resp = {
  scope: string;
  rows: PerfRow[];
  combinations: { combination: string; timeframe: string; size: number; trades: number; win_rate: number | null; profit_factor: number | null; expectancy: number | null; pnl_r: number | null; max_drawdown: number | null }[];
  regime_matrix: Record<string, Record<string, { trades: number; expectancy: number | null; win_rate: number | null; total_r: number | null }>>;
  regimes: string[];
};

const SCOPES: [string, string, string][] = [
  ["DEMO", "DEMO (trades reais na conta demo)", "PnL realizado na conta demo; análise com dados da conta real."],
  ["RECENT", "RECENT (backtest 30 dias)", "Últimos 30 dias da pesquisa, custos simulados."],
  ["HISTORICAL", "HISTORICAL (backtest completo)", "Período completo da pesquisa, custos simulados."],
];

function heat(e: number | null | undefined, n: number) {
  if (e == null || n === 0) return "transparent";
  const a = Math.min(0.55, Math.abs(e) * 1.2) * Math.min(1, n / 10);
  return e > 0 ? `rgba(22,199,132,${a})` : `rgba(234,57,67,${a})`;
}

export default function QuantPerformanceTab() {
  const [scope, setScope] = useState("DEMO");
  const [attr, setAttr] = useState<"primary" | "participant">("primary");
  const [tf, setTf] = useState<string>("");
  const [d, setD] = useState<Resp | null>(null);

  const load = useCallback(() => api<Resp>(`/quant/performance?scope=${scope}`).then(setD), [scope]);
  useEffect(() => { load(); }, [load]);

  const rows = (d?.rows ?? []).filter((r) => r.attribution === attr && (!tf || tfOf(r.strategy_name) === tf));
  const combos = (d?.combinations ?? []).filter((c) => !tf || c.timeframe === tf);
  const matrix = Object.entries(d?.regime_matrix ?? {}).filter(([sid]) => !tf || tfOf(sid) === tf);
  const all = rows.filter((r) => r.direction === "ALL").sort((a, b) => (b.expectancy ?? -9) - (a.expectancy ?? -9));
  const byDir = (sid: string, dir: string) => rows.find((r) => r.strategy_name === sid && r.direction === dir);

  return (
    <>
      <div className="row">
        {SCOPES.map(([k, l]) => <button key={k} className={`btn ${scope === k ? "primary" : ""}`} onClick={() => setScope(k)}>{l}</button>)}
        <span style={{ width: 16 }} />
        <button className={`btn small ${attr === "primary" ? "primary" : ""}`} onClick={() => setAttr("primary")}>Atribuição: primária</button>
        <button className={`btn small ${attr === "participant" ? "primary" : ""}`} onClick={() => setAttr("participant")}>Atribuição: participante</button>
        <span style={{ width: 16 }} />
        <select className="select" value={tf} onChange={(e) => setTf(e.target.value)}>
          <option value="">Todos os timeframes</option>
          {TIMEFRAMES.map((t) => <option key={t} value={t}>{TF_LABEL[t]}</option>)}
        </select>
      </div>
      <Help>{SCOPES.find((s) => s[0] === scope)?.[2]} Os escopos nunca são somados. Primária = o trade conta só para a
        estratégia que liderou; participante = conta para todas que deram sinal (primária + apoio).</Help>
      {!d ? <div className="muted">Carregando...</div> : (
        <>
          <h2 className="section">Desempenho por estratégia</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Estratégia</th><th>Sinais</th><th>Trades</th><th>Acerto</th><th>PF</th><th>Expectativa</th><th>Total R</th><th>Máx. DD</th>
                {scope === "DEMO" && <th>PnL</th>}<th>Duração média</th><th>Melhor / pior</th><th>Últimos 10</th><th>Último trade</th></tr></thead>
              <tbody>
                {all.map((r) => (
                  <tr key={r.strategy_name} className={r.enabled ? "" : "dim"}>
                    <td><b>{r.strategy_name}</b> <span className="badge tf">{tfOf(r.strategy_name)}</span> <span className="muted">v{r.version}</span>{!r.enabled && <span className="badge">desativada</span>}</td>
                    <td>{r.signals}</td><td>{r.executions}</td><td>{r.win_rate != null ? `${fmt(r.win_rate, 1)}%` : "-"}</td>
                    <td>{fmt(r.profit_factor, 2)}</td><td className={cls(r.expectancy)}>{fmtR(r.expectancy)}</td>
                    <td className={cls(r.total_r)}>{fmtR(r.total_r, 1)}</td><td>{fmt(r.max_drawdown, 1)}R</td>
                    {scope === "DEMO" && <td className={cls(r.total_pnl)}>{fmt(r.total_pnl, 2)}</td>}
                    <td>{fmtDuration(r.average_duration)}</td><td>{fmtR(r.best_trade, 1)} / {fmtR(r.worst_trade, 1)}</td>
                    <td>{r.last10 ?? "-"}</td><td>{fmtDate(r.last_trade)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2 className="section">LONG vs SHORT</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Estratégia</th><th>LONG trades</th><th>LONG acerto</th><th>LONG expect.</th><th>LONG total</th><th>SHORT trades</th><th>SHORT acerto</th><th>SHORT expect.</th><th>SHORT total</th></tr></thead>
              <tbody>
                {all.map((r) => {
                  const l = byDir(r.strategy_name, "LONG");
                  const s = byDir(r.strategy_name, "SHORT");
                  return (
                    <tr key={r.strategy_name}>
                      <td>{r.strategy_name}</td>
                      <td>{l?.executions ?? 0}</td><td>{l?.win_rate != null ? `${fmt(l.win_rate, 1)}%` : "-"}</td>
                      <td className={cls(l?.expectancy)}>{fmtR(l?.expectancy)}</td><td className={cls(l?.total_r)}>{fmtR(l?.total_r, 1)}</td>
                      <td>{s?.executions ?? 0}</td><td>{s?.win_rate != null ? `${fmt(s.win_rate, 1)}%` : "-"}</td>
                      <td className={cls(s?.expectancy)}>{fmtR(s?.expectancy)}</td><td className={cls(s?.total_r)}>{fmtR(s?.total_r, 1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h2 className="section">Matriz estratégia × regime do BTC (expectativa por trade)</h2>
          <div className="table-wrap">
            <table className="matrix">
              <thead><tr><th>Estratégia</th>{d.regimes.map((r) => <th key={r}>{REGIME_SHORT[r]}</th>)}</tr></thead>
              <tbody>
                {matrix.map(([sid, regs]) => (
                  <tr key={sid}>
                    <td>{sid}</td>
                    {d.regimes.map((r) => {
                      const c = regs[r];
                      return <td key={r} style={{ background: heat(c?.expectancy, c?.trades ?? 0) }}
                        title={c ? `${c.trades} trades · acerto ${fmt(c.win_rate, 1)}% · total ${fmtR(c.total_r, 1)}` : "sem trades"}>
                        {c && c.trades ? <>{fmtR(c.expectancy)} <span className="muted">({c.trades})</span></> : <span className="muted">-</span>}
                      </td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Help>Células com poucos trades aparecem mais claras: o número entre parênteses é a amostra. Use esta matriz para
            decidir os regimes aceitos de cada estratégia (o bot não muda isso sozinho).</Help>

          <h2 className="section">Combinações de estratégias (apenas medição)</h2>
          {combos.length === 0 ? <Empty>Sem trades com 2+ estratégias neste escopo ainda.</Empty> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Combinação</th><th>Trades</th><th>Acerto</th><th>PF</th><th>Expectativa</th><th>Total R</th><th>Máx. DD</th></tr></thead>
                <tbody>
                  {combos.map((c) => (
                    <tr key={c.timeframe + c.combination}>
                      <td>{c.combination} <span className="badge tf">{c.timeframe}</span></td><td>{c.trades}</td><td>{c.win_rate != null ? `${fmt(c.win_rate, 1)}%` : "-"}</td>
                      <td>{fmt(c.profit_factor, 2)}</td><td className={cls(c.expectancy)}>{fmtR(c.expectancy)}</td>
                      <td className={cls(c.pnl_r)}>{fmtR(c.pnl_r, 1)}</td><td>{fmt(c.max_drawdown, 1)}R</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Help>Combinações são só medidas; nenhuma vira estratégia automaticamente.</Help>
        </>
      )}
    </>
  );
}
