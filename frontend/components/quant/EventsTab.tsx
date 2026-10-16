"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Empty, Help } from "./ui";

type Ev = { id: number; created_at: string; event_type: string; symbol: string | null; strategy: string | null; trade_id: number | null; details: Record<string, unknown> };

const TYPES = ["", "TRADE_OPENED", "TRADE_UPDATED", "TRADE_CLOSED", "OPPORTUNITY_RANKED", "SIGNAL_GENERATED", "REGIME_CHANGED",
  "STRATEGY_ENABLED", "STRATEGY_DISABLED", "STRATEGY_UPDATED", "RESEARCH_FINISHED", "REPORT_GENERATED",
  "LIVE_CONFIGURATION_DETECTED", "ERROR"];

const COLOR: Record<string, string> = { ERROR: "red", LIVE_CONFIGURATION_DETECTED: "red", TRADE_OPENED: "green", TRADE_CLOSED: "blue", REGIME_CHANGED: "orange" };

export default function EventsTab() {
  const [type, setType] = useState("");
  const [symbol, setSymbol] = useState("");
  const [rows, setRows] = useState<Ev[]>([]);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(() => {
    const qs = new URLSearchParams({ ...(type && { event_type: type }), ...(symbol && { symbol }) }).toString();
    api<Ev[]>(`/quant/events${qs ? `?${qs}` : ""}`).then(setRows);
  }, [type, symbol]);
  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <>
      <Help>Log estruturado do motor: cada sinal, oportunidade ranqueada, abertura, atualização e fechamento de trade,
        mudança de regime e alteração de estratégia. Clique numa linha para ver os detalhes.</Help>
      <div className="row">
        <select className="select" value={type} onChange={(e) => setType(e.target.value)}>
          {TYPES.map((t) => <option key={t} value={t}>{t || "Todos os tipos"}</option>)}
        </select>
        <input className="select" placeholder="Par (ex.: ETH-USDT)" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
      </div>
      {rows.length === 0 ? <Empty>Nenhum evento.</Empty> : (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <thead><tr><th>Quando</th><th>Tipo</th><th>Par</th><th>Estratégia</th><th>Trade</th><th>Mensagem</th></tr></thead>
            <tbody>
              {rows.map((e) => (
                <Fragment key={e.id}>
                  <tr className="clickable" onClick={() => setOpen(open === e.id ? null : e.id)}>
                    <td>{fmtDate(e.created_at)}</td><td><span className={`badge ${COLOR[e.event_type] ?? ""}`}>{e.event_type}</span></td>
                    <td>{e.symbol ?? "-"}</td><td>{e.strategy ?? "-"}</td><td>{e.trade_id ?? "-"}</td>
                    <td className="wrap">{String(e.details?.message ?? "")}</td>
                  </tr>
                  {open === e.id && <tr><td colSpan={6} className="details"><pre className="json">{JSON.stringify(e.details, null, 2)}</pre></td></tr>}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
