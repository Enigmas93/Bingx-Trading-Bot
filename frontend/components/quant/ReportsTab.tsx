"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import { EXIT_LABEL, REGIME_SHORT, cls, fmtR } from "@/lib/quant";
import { Empty, Health, Help, Tag } from "./ui";

type ScopeRow = { strategy: string; version: string } & Record<string, { trades: number; win_rate: number | null; pf: number | null; expectancy_r: number | null; total_r: number | null; pnl: number | null } | string>;
type Report = {
  id: number; date: string; created_at: string;
  content: {
    date: string; disclaimer: string;
    DEMO: { opened: number; closed: number; open_now: number; pnl: number; r: number; wins: number; losses: number; pnl_unconfirmed: number;
      by_strategy: Record<string, number>; by_exit_reason: Record<string, number>; by_direction: Record<string, number>;
      trades: { uid: string; symbol: string; direction: string; strategy: string; confluence: string; exit_reason: string; pnl: number | null; r: number | null }[] };
    RECENT: ScopeRow[]; HISTORICAL: ScopeRow[];
    regimes: Record<string, number>; regime_changes: number; events: Record<string, number>;
    strategies: { strategy: string; enabled: boolean; health: string; reason: string | null; tags: string[] }[];
  };
};

function ScopeTable({ rows }: { rows: ScopeRow[] }) {
  if (!rows.length) return <Empty>Sem dados (rode a pesquisa).</Empty>;
  const cell = (r: ScopeRow, k: string) => r[k] as { trades: number; expectancy_r: number | null; win_rate: number | null } | undefined;
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Estratégia</th><th>Trades</th><th>Acerto</th><th>Expectativa</th><th>LONG</th><th>SHORT</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.strategy}><td>{r.strategy}</td><td>{cell(r, "ALL")?.trades ?? 0}</td>
          <td>{cell(r, "ALL")?.win_rate != null ? `${fmt(cell(r, "ALL")!.win_rate, 1)}%` : "-"}</td>
          <td className={cls(cell(r, "ALL")?.expectancy_r)}>{fmtR(cell(r, "ALL")?.expectancy_r)}</td>
          <td className={cls(cell(r, "LONG")?.expectancy_r)}>{fmtR(cell(r, "LONG")?.expectancy_r)} ({cell(r, "LONG")?.trades ?? 0})</td>
          <td className={cls(cell(r, "SHORT")?.expectancy_r)}>{fmtR(cell(r, "SHORT")?.expectancy_r)} ({cell(r, "SHORT")?.trades ?? 0})</td></tr>
      ))}</tbody>
    </table></div>
  );
}

export default function ReportsTab() {
  const [list, setList] = useState<Report[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<Report[]>("/quant/reports").then((l) => { setList(l); setSel((s) => s ?? l[0]?.id ?? null); }), []);
  useEffect(() => { load(); }, [load]);

  const generate = async (today: boolean) => {
    setBusy(true);
    const day = today ? new Date().toISOString().slice(0, 10) : "";
    await api(`/quant/reports${day ? `?day=${day}` : ""}`, { method: "POST" });
    setSel(null);
    await load();
    setBusy(false);
  };

  const rep = list.find((r) => r.id === sel);
  const c = rep?.content;
  return (
    <>
      <Help>Relatório diário gerado às 00:05 UTC. As seções DEMO, RECENT e HISTORICAL são separadas e nunca somadas.</Help>
      <div className="row">
        <select className="select" value={sel ?? ""} onChange={(e) => setSel(Number(e.target.value))}>
          {list.map((r) => <option key={r.id} value={r.id}>{r.date} (gerado {fmtDate(r.created_at)})</option>)}
        </select>
        <button className="btn" disabled={busy} onClick={() => generate(true)}>Gerar parcial de hoje</button>
        <button className="btn" disabled={busy} onClick={() => generate(false)}>Gerar de ontem</button>
      </div>
      {!c ? <Empty>Nenhum relatório ainda.</Empty> : (
        <>
          <div className="alert warn">{c.disclaimer}</div>
          <h2 className="section">DEMO — {c.date}</h2>
          <div className="grid">
            <div className="card"><h3>Trades</h3><div className="value">{c.DEMO.closed}</div><div className="sub">{c.DEMO.opened} abertos no dia · {c.DEMO.open_now} em aberto agora</div></div>
            <div className="card"><h3>Resultado</h3><div className={`value ${cls(c.DEMO.pnl)}`}>{fmt(c.DEMO.pnl, 2)}</div><div className="sub">{fmtR(c.DEMO.r)} · {c.DEMO.wins}W/{c.DEMO.losses}L{c.DEMO.pnl_unconfirmed ? ` · ${c.DEMO.pnl_unconfirmed} sem confirmação` : ""}</div></div>
            <div className="card"><h3>LONG / SHORT</h3><div className="value small">{fmtR(c.DEMO.by_direction.LONG)} / {fmtR(c.DEMO.by_direction.SHORT)}</div></div>
            <div className="card"><h3>Regime do BTC no dia</h3><div className="sub">{Object.entries(c.regimes).map(([k, v]) => `${REGIME_SHORT[k] ?? k} ${v}h`).join(" · ") || "-"}</div><div className="sub">{c.regime_changes} mudanças</div></div>
          </div>
          {c.DEMO.trades.length > 0 && (
            <div className="table-wrap"><table>
              <thead><tr><th>Trade</th><th>Par</th><th>Direção</th><th>Primária</th><th>Confluência</th><th>Saída</th><th>R</th><th>PnL</th></tr></thead>
              <tbody>{c.DEMO.trades.map((t) => (
                <tr key={t.uid}><td>{t.uid}</td><td>{t.symbol}</td><td>{t.direction}</td><td>{t.strategy}</td><td>{t.confluence}</td>
                  <td>{EXIT_LABEL[t.exit_reason] ?? t.exit_reason}</td><td className={cls(t.r)}>{fmtR(t.r)}</td><td className={cls(t.pnl)}>{fmt(t.pnl, 4)}</td></tr>
              ))}</tbody>
            </table></div>
          )}
          <h2 className="section">RECENT (backtest dos últimos 30 dias)</h2>
          <ScopeTable rows={c.RECENT} />
          <h2 className="section">HISTORICAL (backtest completo)</h2>
          <ScopeTable rows={c.HISTORICAL} />
          <h2 className="section">Estado das estratégias</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>Estratégia</th><th>Ativa</th><th>Saúde</th><th>Classificação</th><th>Motivo</th></tr></thead>
            <tbody>{c.strategies.map((s) => (
              <tr key={s.strategy}><td>{s.strategy}</td><td>{s.enabled ? "sim" : "não"}</td><td><Health h={s.health} /></td>
                <td>{(s.tags ?? []).map((t) => <Tag key={t} t={t} />)}</td><td className="wrap">{s.reason}</td></tr>
            ))}</tbody>
          </table></div>
          <div className="muted small">Eventos do dia: {Object.entries(c.events).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
        </>
      )}
    </>
  );
}
