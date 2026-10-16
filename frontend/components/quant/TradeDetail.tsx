"use client";

import { useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import { COMPONENT_LABEL, EXIT_LABEL, cls, fmtDuration, fmtR, LEVEL_LABEL } from "@/lib/quant";
import { Conditions, Dir, Level, Modal, Regime, ScoreBar } from "./ui";

type Detail = {
  trade: Record<string, any>;
  features_at_entry: { features: Record<string, number | null>; signals: any[]; regime: Record<string, any>; opportunity: Record<string, any>; created_at: string } | null;
  position: Record<string, any> | null;
  events: { id: number; created_at: string; event_type: string; details: Record<string, any> }[];
};

export default function TradeDetail({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged?: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [showFeats, setShowFeats] = useState(false);

  const load = () => api<Detail>(`/quant/journal/${id}`).then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [id]);

  const manualClose = async () => {
    if (!confirm("Fechar esta posição na conta DEMO agora (MANUAL_DEMO_EXIT)?")) return;
    setClosing(true);
    try {
      await api(`/quant/journal/${id}/close`, { method: "POST" });
      await load();
      onChanged?.();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setClosing(false);
    }
  };

  const t = d?.trade;
  const ex = t?.explanation ?? {};
  return (
    <Modal wide onClose={onClose} title={t ? <span className="row"><b>{t.symbol}</b> <Dir d={t.direction} /> <span className="badge tf">{t.timeframe}</span> <span className="badge">{t.status}</span> <span className="muted">{t.trade_uid}</span></span> : "Trade"}>
      {err && <div className="alert error">{err}</div>}
      {!t ? <div className="muted">Carregando...</div> : (
        <>
          <div className="explain">
            <div className="explain-title">Por que o bot entrou</div>
            <div>{ex.summary}</div>
          </div>

          <div className="grid">
            <div className="card"><h3>Entrada</h3><div className="value">{fmt(t.entry_price, 6)}</div>
              <div className="sub">referência conta real {fmt(t.expected_entry_price, 6)} · slippage {fmt(t.slippage_pct, 3)}%</div></div>
            <div className="card"><h3>Stop / Alvo</h3><div className="value">{fmt(t.current_stop ?? t.stop_reference, 6)}</div>
              <div className="sub">alvo {fmt(t.target_reference, 6)} {t.breakeven_set && "· stop no breakeven"}</div></div>
            <div className="card"><h3>Resultado</h3>
              <div className={`value ${cls(t.pnl)}`}>{t.status === "OPEN" ? fmtR(d?.position?.unrealized_r) : fmtR(t.pnl_r)}</div>
              <div className="sub">{t.status === "OPEN" ? `PnL não realizado ${fmt(d?.position?.unrealized_pnl, 4)}` :
                `PnL ${fmt(t.pnl, 4)} ${t.pnl_confirmed ? "(real, confirmado)" : "(aguardando confirmação)"} · taxas ${fmt(t.fees, 4)} · funding ${fmt(t.funding, 4)}`}</div></div>
            <div className="card"><h3>Saída</h3><div className="value small">{t.exit_reason ? EXIT_LABEL[t.exit_reason] ?? t.exit_reason : "em aberto"}</div>
              <div className="sub">{t.exit_detail ?? ""} {t.duration_minutes != null && `· ${fmtDuration(t.duration_minutes)}`}</div></div>
            <div className="card"><h3>MFE / MAE</h3><div className="value small">{fmtR(t.mfe_r)} / {fmtR(t.mae_r)}</div>
              <div className="sub">máximo a favor / contra durante o trade</div></div>
          </div>

          <div className="two-col">
            <div>
              <h4>Estratégias ({LEVEL_LABEL[t.confluence_level]})</h4>
              <div className="sub muted">grupo {t.confluence_group_id}</div>
              {(ex.strategies ?? []).map((s: any) => (
                <div key={s.strategy_id} className="strat-block">
                  <div className="row"><b>{s.strategy_id}</b> <span className="muted">v{s.version}</span>
                    <span className={`badge ${s.role === "primária" ? "blue" : ""}`}>{s.role}</span>
                    <span>score {s.score}</span><ScoreBar value={s.score} /></div>
                  <div className="muted small">{s.reason}</div>
                  <Conditions items={s.conditions} />
                </div>
              ))}
            </div>
            <div>
              <h4>Score final {fmt(t.final_score, 1)} / 100</h4>
              <table className="mini">
                <tbody>
                  {Object.entries(ex.score_breakdown ?? {}).map(([k, v]) => (
                    <tr key={k}><td>{COMPONENT_LABEL[k] ?? k}</td><td>{fmt(v as number, 0)}</td>
                      <td><ScoreBar value={v as number} /></td><td className="muted">peso {fmt(ex.weights?.[k], 2)}</td></tr>
                  ))}
                </tbody>
              </table>
              <h4>Mercado na entrada</h4>
              <div className="kv-list">
                <div><span>Regime BTC</span><Regime r={t.btc_regime} long={ex.market?.btc_label} /></div>
                <div><span>Estado do BTC</span>{t.market_state}</div>
                <div><span>Estado do par</span>{t.market_regime}</div>
                <div><span>Spread</span>{fmt(ex.market?.spread_pct, 4)}%</div>
                <div><span>Funding</span>{fmt(ex.market?.funding_pct, 4)}%</div>
                <div><span>Risco</span>{ex.risk}</div>
                <div><span>Notícias</span>{ex.news ? `${ex.news.impact}: ${ex.news.summary ?? ""}` : "não verificadas"}</div>
                <div><span>Dados</span>{ex.data_source} → execução {ex.execution}</div>
              </div>
              <h4>Decisão</h4>
              <ul className="conds">{(ex.decision ?? []).map((r: string, i: number) => <li key={i} className="ok">• {r}</li>)}</ul>
            </div>
          </div>

          <h4>Linha do tempo</h4>
          <div className="timeline">
            {d!.events.map((e) => (
              <div key={e.id}><span className="muted">{fmtDate(e.created_at)}</span> <b>{e.event_type}</b> {e.details?.message ?? ""}</div>
            ))}
            {(t.notes ?? []).map((n: any, i: number) => (
              <div key={`n${i}`}><span className="muted">{fmtDate(n.at)}</span> nota: {n.text}</div>
            ))}
          </div>

          <h4 className="clickable" onClick={() => setShowFeats(!showFeats)}>
            {showFeats ? "▾" : "▸"} Snapshot imutável das features na entrada
          </h4>
          {showFeats && d!.features_at_entry && (
            <div className="details">
              <div className="kv">
                {Object.entries(d!.features_at_entry.features).map(([k, v]) => (
                  <div key={k}><span>{k}</span>{v === null ? "-" : fmt(v, 6)}</div>
                ))}
              </div>
              <div className="muted small" style={{ marginTop: 8 }}>
                Gravado em {fmtDate(d!.features_at_entry.created_at)}. Este registro não pode ser alterado.
              </div>
            </div>
          )}

          {t.status === "OPEN" && (
            <div className="row" style={{ marginTop: 16 }}>
              <button className="btn danger" disabled={closing} onClick={manualClose}>
                {closing ? "Fechando..." : "Fechar na DEMO (saída manual)"}
              </button>
              <span className="muted small">Stop verificado na corretora em {fmtDate(d?.position?.sl_verified_at)}</span>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
