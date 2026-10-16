"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { api, fmt } from "@/lib/api";
import { COMPONENT_LABEL, OPP_STATUS, Opportunity, SIGNAL_STATUS, StrategySignal, TIMEFRAMES, TF_LABEL, fmtCandle } from "@/lib/quant";
import { Conditions, Dir, Empty, Help, Level, Regime, ScoreBar } from "./ui";

type Resp = { candle_time: number | null; candles: number[]; opportunities: Opportunity[]; signals: StrategySignal[]; signal_status: Record<string, number> };

export default function OpportunitiesTab() {
  const [data, setData] = useState<Resp | null>(null);
  const [candle, setCandle] = useState<number | null>(null);
  const [tf, setTf] = useState<string>("1h");
  const [open, setOpen] = useState<number | null>(null);
  const [sigFilter, setSigFilter] = useState<string>("");

  const load = useCallback(() => {
    api<Resp>(`/quant/opportunities?timeframe=${tf}${candle ? `&candle_time=${candle}` : ""}`).then(setData);
  }, [candle, tf]);
  useEffect(() => { load(); }, [load]);

  if (!data) return <div className="muted">Carregando...</div>;
  const sigsOf = (o: Opportunity) => data.signals.filter((s) => s.opportunity_id === o.id);
  const shownSignals = data.signals.filter((s) => !sigFilter || s.status === sigFilter)
    .sort((a, b) => b.score - a.score);

  return (
    <>
      <div className="row between">
        <Help>
          A cada candle fechado (1h, 15m e 5m, separadamente), cada variante de estratégia avalia LONG e SHORT em todos os pares. Sinais aceitos do mesmo
          par e direção formam uma oportunidade (confluência), que recebe um score 0-100 e é ranqueada. Toda
          oportunidade mostra o motivo da decisão.
        </Help>
        <div className="row">
          {TIMEFRAMES.map((t) => (
            <button key={t} className={`btn ${tf === t ? "primary" : ""}`} onClick={() => { setTf(t); setCandle(null); }}>{TF_LABEL[t]}</button>
          ))}
        </div>
        <div className="field" style={{ minWidth: 200 }}>
          <label>Candle ({tf})</label>
          <select value={candle ?? data.candle_time ?? ""} onChange={(e) => setCandle(Number(e.target.value))}>
            {data.candles.map((c) => <option key={c} value={c}>{fmtCandle(c)}</option>)}
          </select>
        </div>
      </div>

      <h2 className="section">Oportunidades ranqueadas ({data.opportunities.length})</h2>
      {data.opportunities.length === 0 ? <Empty>Nenhuma oportunidade neste candle.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>#</th><th>Par</th><th>Direção</th><th>Score final</th><th>Base</th><th>Regime</th><th>Confluência</th><th>Primária</th><th>Apoio</th><th>Status</th><th>Motivo</th></tr></thead>
            <tbody>
              {data.opportunities.map((o) => (
                <Fragment key={o.id}>
                  <tr className="clickable" onClick={() => setOpen(open === o.id ? null : o.id)}>
                    <td>{o.rank}</td><td><b>{o.symbol}</b></td><td><Dir d={o.direction} /></td>
                    <td>{fmt(o.final_score, 1)}<ScoreBar value={o.final_score} /></td>
                    <td>{fmt(o.base_score, 0)}</td><td><Regime r={o.btc_regime} /> {fmt(o.regime_score, 0)}</td>
                    <td><Level l={o.confluence_level} /></td><td>{o.primary_strategy}</td>
                    <td>{o.supporting_strategies.join(", ") || "-"}</td>
                    <td><span className={`badge ${OPP_STATUS[o.status]?.color ?? ""}`}>{OPP_STATUS[o.status]?.label ?? o.status}</span></td>
                    <td className="wrap">{o.decision_reasons[o.decision_reasons.length - 1]}</td>
                  </tr>
                  {open === o.id && (
                    <tr><td colSpan={11} className="details">
                      <div className="two-col">
                        <div>
                          <h4>Composição do score</h4>
                          <table className="mini"><tbody>
                            {Object.entries(o.score_components).filter(([k]) => k !== "weights").map(([k, v]) => (
                              <tr key={k}><td>{COMPONENT_LABEL[k] ?? k}</td><td>{fmt(v as number, 0)}</td><td><ScoreBar value={v as number} /></td>
                                <td className="muted">peso {fmt((o.score_components.weights as Record<string, number>)?.[k], 2)}</td></tr>
                            ))}
                          </tbody></table>
                          <h4>Decisão</h4>
                          <ul className="conds">{o.decision_reasons.map((r, i) => <li key={i} className="ok">• {r}</li>)}</ul>
                        </div>
                        <div>
                          <h4>Sinais que formaram a oportunidade</h4>
                          {sigsOf(o).map((s) => (
                            <div key={s.id} className="strat-block">
                              <div className="row"><b>{s.strategy_id}</b><span>score {fmt(s.score, 0)}</span>
                                <span className="muted">ref {fmt(s.ref_price, 6)} · stop {fmt(s.stop, 6)} · alvo {fmt(s.target, 6)} ({fmt(s.expected_r, 2)}R)</span></div>
                              <div className="muted small">{s.reason}</div>
                              <Conditions items={s.conditions} />
                            </div>
                          ))}
                        </div>
                      </div>
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="section">Todos os sinais do candle ({data.signals.length})</h2>
      <div className="chips">
        <span className={`chip ${sigFilter === "" ? "on" : ""}`} onClick={() => setSigFilter("")}>Todos</span>
        {Object.entries(data.signal_status).map(([k, v]) => (
          <span key={k} className={`chip ${sigFilter === k ? "on" : ""}`} title={SIGNAL_STATUS[k]?.help} onClick={() => setSigFilter(k)}>
            {SIGNAL_STATUS[k]?.label ?? k}: {v}
          </span>
        ))}
      </div>
      {shownSignals.length === 0 ? <Empty>Nenhum sinal.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Estratégia</th><th>Par</th><th>Direção</th><th>Score</th><th>R esperado</th><th>Status</th><th>Motivo</th></tr></thead>
            <tbody>
              {shownSignals.map((s) => (
                <tr key={s.id}>
                  <td>{s.strategy_id}</td><td>{s.symbol}</td><td><Dir d={s.direction} /></td><td>{fmt(s.score, 0)}</td>
                  <td>{fmt(s.expected_r, 2)}</td>
                  <td><span className={`badge ${SIGNAL_STATUS[s.status]?.color ?? ""}`} title={SIGNAL_STATUS[s.status]?.help}>{SIGNAL_STATUS[s.status]?.label ?? s.status}</span></td>
                  <td className="wrap">{s.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
