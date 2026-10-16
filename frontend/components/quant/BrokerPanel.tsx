"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import { cls } from "@/lib/quant";
import { Dir, Empty, Help } from "./ui";

export type BrokerPosition = {
  symbol: string; direction: "LONG" | "SHORT"; quantity: number; entry_price: number; mark_price: number | null;
  unrealized_pnl: number; leverage: number | null; margin: number; notional: number;
  stop_order: number | null; target_order: number | null; status: "OK" | "SEM_STOP" | "SEM_TRADE";
  trade: { id: number; uid: string; strategy: string; timeframe: string; stop: number; target: number } | null;
};
export type BrokerSnapshot = {
  ok: boolean; error?: string; checked_at: string;
  balance?: { asset: string; balance: number; equity: number; available: number; used_margin: number; unrealized_pnl: number };
  positions?: BrokerPosition[]; trades_without_position?: { symbol: string; direction: string; trade_id: number }[];
  unrealized_pnl?: number; issues?: number;
};
export type BrokerResult = {
  ok: boolean; error?: string; since: string | null; broker: Record<string, number>; broker_net: number;
  journal_net: number; journal_trades: number; journal_r: number; unconfirmed_trades: number; unconfirmed_pnl: number;
  difference: number; explained: { open_positions_fees: number; legacy_positions: number }; unexplained: number;
  checked_at: string;
};

const STATUS: Record<string, { label: string; color: string; help: string }> = {
  OK: { label: "Protegida", color: "green", help: "posição do bot com stop ativo na corretora" },
  SEM_STOP: { label: "SEM STOP", color: "red", help: "sem ordem de stop na corretora — o monitor recoloca em até 30 s" },
  SEM_TRADE: { label: "Fora do bot", color: "orange", help: "posição sem trade no diário (aberta manualmente?) — stop de emergência colocado" },
};

export function useBroker(intervalMs = 15000) {
  const [snap, setSnap] = useState<BrokerSnapshot | null>(null);
  const [res, setRes] = useState<BrokerResult | null>(null);
  const load = useCallback(async (refresh = false) => {
    const q = refresh ? "?refresh=true" : "";
    try { setSnap(await api<BrokerSnapshot>(`/quant/broker${q}`)); } catch { /* mantém o último */ }
    try { setRes(await api<BrokerResult>(`/quant/broker/result${q}`)); } catch { /* idem */ }
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(() => load(), intervalMs);
    return () => clearInterval(id);
  }, [load, intervalMs]);
  return { snap, res, reload: load };
}

export function BrokerCards({ snap, res }: { snap: BrokerSnapshot | null; res: BrokerResult | null }) {
  const b = snap?.balance;
  return (
    <>
      <div className="card">
        <h3>Saldo na corretora ({b?.asset ?? "VST"})</h3>
        <div className="value">{fmt(b?.equity, 2)}</div>
        <div className="sub">saldo {fmt(b?.balance, 2)} · disponível {fmt(b?.available, 2)} · margem {fmt(b?.used_margin, 2)}</div>
      </div>
      <div className="card">
        <h3>Resultado real (corretora)</h3>
        <div className={`value ${cls(res?.broker_net)}`}>{res?.ok ? fmt(res.broker_net, 2) : "-"}</div>
        <div className="sub">
          {res?.ok ? <>PnL realizado {fmt(res.broker.REALIZED_PNL, 2)} · taxas {fmt(res.broker.TRADING_FEE, 2)} · funding {fmt(res.broker.FUNDING_FEE, 2)}
            <br />aberto agora {fmt(snap?.unrealized_pnl, 2)} · desde {fmtDate(res.since)}</> : res?.error}
        </div>
        {res?.ok && (
          <div className={`sub ${Math.abs(res.unexplained) > 0.05 ? "neg" : "muted"}`}
            title="Diário = soma dos trades fechados e confirmados. A diferença para a corretora tem partes conhecidas: taxas já cobradas de posições abertas e as posições do sistema anterior fechadas em 03/10.">
            diário {fmt(res.journal_net, 2)} ({res.journal_trades} trades) · diferença {fmt(res.difference, 2)}
            {" "}= posições abertas {fmt(-res.explained.open_positions_fees, 2)} + sistema antigo {fmt(-res.explained.legacy_positions, 2)}
            {Math.abs(res.unexplained) > 0.05 ? ` + NÃO EXPLICADA ${fmt(res.unexplained, 2)}` : " ✓ confere"}
          </div>
        )}
      </div>
    </>
  );
}

export default function BrokerPositions({ snap, reload, onOpenTrade }: {
  snap: BrokerSnapshot | null; reload: (refresh?: boolean) => void; onOpenTrade: (id: number) => void;
}) {
  if (!snap) return <div className="muted">Lendo a corretora...</div>;
  if (!snap.ok) return <div className="alert error">Não foi possível ler a corretora: {snap.error}</div>;
  const pos = snap.positions ?? [];
  return (
    <>
      {(snap.issues ?? 0) > 0 && (
        <div className="alert error">
          {snap.issues} divergência(s) entre a corretora e o diário. O monitor corrige automaticamente (recoloca stops,
          reabre trades marcados errado e protege posições de fora); acompanhe em Eventos.
        </div>
      )}
      {pos.length === 0 ? <Empty>Nenhuma posição aberta na corretora.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Par</th><th>Direção</th><th>Qtd</th><th>Entrada</th><th>Atual</th><th>PnL</th><th>Stop</th><th>Alvo</th><th>Estratégia</th><th>Situação</th></tr></thead>
            <tbody>
              {pos.map((p) => (
                <tr key={p.symbol + p.direction} className={p.trade ? "clickable" : ""} onClick={() => p.trade && onOpenTrade(p.trade.id)}>
                  <td><b>{p.symbol}</b></td><td><Dir d={p.direction} /></td><td>{fmt(p.quantity, 4)}</td>
                  <td>{fmt(p.entry_price, 6)}</td><td>{fmt(p.mark_price, 6)}</td>
                  <td className={cls(p.unrealized_pnl)}>{fmt(p.unrealized_pnl, 3)}</td>
                  <td>{fmt(p.stop_order, 6)}</td><td>{fmt(p.target_order, 6)}</td>
                  <td>{p.trade ? <>{p.trade.strategy} <span className="badge tf">{p.trade.timeframe}</span></> : <span className="muted">—</span>}</td>
                  <td><span className={`badge ${STATUS[p.status].color}`} title={STATUS[p.status].help}>{STATUS[p.status].label}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="row between" style={{ marginTop: 6 }}>
        <Help>Lido direto da BingX (conta DEMO) a cada 15 s — é exatamente o que está na corretora. Atualizado {fmtDate(snap.checked_at)}.</Help>
        <button className="btn small" onClick={() => reload(true)}>Atualizar agora</button>
      </div>
      {(snap.trades_without_position ?? []).length > 0 && (
        <Help>Fechamento em confirmação: {snap.trades_without_position!.map((t) => t.symbol).join(", ")} (o bot só dá o trade
          como fechado depois de confirmar na corretora).</Help>
      )}
    </>
  );
}
