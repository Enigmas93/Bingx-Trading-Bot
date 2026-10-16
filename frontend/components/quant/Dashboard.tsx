"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import {
  EXIT_LABEL, Overview, RiskStatus, REGIME_COLOR, REGIME_SHORT, SIGNAL_STATUS, StrategiesResponse, TIMEFRAMES, cls,
  fmtCandle, fmtDuration, fmtR,
} from "@/lib/quant";
import { Dir, Empty, Help, Level, Regime } from "./ui";
import StrategyGroups from "./StrategyGroups";
import BrokerPositions, { BrokerCards, useBroker } from "./BrokerPanel";
import TradeDetail from "./TradeDetail";
import StrategyDetail from "./StrategyDetail";

export default function Dashboard({ goTo }: { goTo: (tab: string) => void }) {
  const [ov, setOv] = useState<Overview | null>(null);
  const [st, setSt] = useState<StrategiesResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [trade, setTrade] = useState<number | null>(null);
  const [strategy, setStrategy] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const broker = useBroker();
  const [health, setHealth] = useState<{ stale: boolean; network_problem: boolean; last_cycle_minutes_ago: number | null;
    recent_errors: { at: string; message: string }[] } | null>(null);
  const [risk, setRisk] = useState<RiskStatus | null>(null);

  const load = useCallback(async () => {
    try {
      const [o, s] = await Promise.all([api<Overview>("/quant/overview"), api<StrategiesResponse>("/quant/strategies")]);
      setOv(o);
      setSt(s);
      setRisk(await api<RiskStatus>("/quant/risk"));
      setHealth(await api("/quant/health"));
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 20000);
    return () => clearInterval(id);
  }, [load]);

  const toggle = async (sid: string, enabled: boolean) => {
    await api(`/quant/strategies/${sid}`, { method: "PUT", body: JSON.stringify({ enabled }) });
    load();
  };

  const resetRisk = async () => {
    if (!confirm("Liberar novas entradas agora? A perda do dia e a sequência de perdas voltam a contar a partir deste momento (o histórico não é apagado).")) return;
    await api("/quant/risk/reset", { method: "POST" });
    load();
  };

  const runNow = async () => {
    setRunning(true);
    await api("/quant/run", { method: "POST" });
    setTimeout(() => { load(); setRunning(false); }, 15000);
  };

  if (err) return <div className="alert error">{err}</div>;
  if (!ov || !st) return <div className="muted">Carregando...</div>;

  const reg = ov.regime;
  const det = (reg?.details ?? {}) as Record<string, number | string | null>;

  return (
    <>
      {!ov.engine_enabled && (
        <div className="alert warn">
          Modo observação: o motor analisa e registra sinais/oportunidades, mas a execução automática na DEMO está
          desligada (Motor &amp; Segurança).
        </div>
      )}

      {health && (health.network_problem || health.stale) && (
        <div className="alert error">
          {health.network_problem ? "Problema de conexão com a BingX (internet do PC?) nos últimos 30 min. " : ""}
          {health.stale ? `O motor não completa um ciclo há ${health.last_cycle_minutes_ago ?? "?"} min — verifique se o backend e o PC estão ligados. ` : ""}
          {health.recent_errors[0] && <span className="muted">Último erro: {health.recent_errors[0].message}</span>}
        </div>
      )}
      {risk?.blocked_hours && (
        <div className="alert warn">
          Novas entradas pausadas: {risk.blocked_hours}. Posições abertas continuam com stop/alvo e saídas do motor.
        </div>
      )}
      {!risk?.blocked_hours && risk?.risk_paused && (
        <div className="alert error row between">
          <span>Novas entradas pausadas por risco: {risk.risk_paused}. Volta sozinho às 00:00 UTC.</span>
          <button className="btn small" onClick={resetRisk}>Liberar agora</button>
        </div>
      )}

      <h2 className="section">Visão de mercado</h2>
      <div className="grid">
        <div className="card wide-2">
          <h3>Regime do BTC (candle 1h fechado)</h3>
          {reg ? (
            <>
              <div className="value" style={{ color: REGIME_COLOR[reg.btc_regime] }}>{reg.label}</div>
              <div className="sub">
                Estado {reg.market_state} · ADX 1h {fmt(det.adx_1h as number, 1)} · ATR% {fmt(det.atr_pct_1h as number, 3)}
                {" "}(x{fmt(det.expansion_ratio as number, 2)} da referência) · tendência 4h {String(det.trend_4h ?? "-")}
                {" "}· BTC 24h {fmt(det.btc_ret_24h_pct as number, 2)}%
              </div>
              <div className="regime-strip" title="Últimas 48 horas (cada bloco = 1 candle de 1h)">
                {ov.regime_history.map((h) => (
                  <span key={h.candle_time} style={{ background: REGIME_COLOR[h.btc_regime] }}
                    title={`${fmtCandle(h.candle_time)} · ${REGIME_SHORT[h.btc_regime]}`} />
                ))}
              </div>
              <div className="legend">
                {Object.keys(REGIME_SHORT).map((r) => (
                  <span key={r}><i style={{ background: REGIME_COLOR[r] }} />{REGIME_SHORT[r]}</span>
                ))}
              </div>
            </>
          ) : <div className="muted">Aguardando o primeiro ciclo.</div>}
        </div>
        <div className="card">
          <h3>Últimos ciclos (por timeframe)</h3>
          <table className="mini">
            <tbody>
              {TIMEFRAMES.map((tf) => {
                const c = ov.last_cycles[tf];
                const on = ov.timeframes.includes(tf);
                const sig = c ? Object.entries(c.signals).filter(([k]) => k !== "DUPLICATE") : [];
                return (
                  <tr key={tf} className={on ? "" : "dim"}>
                    <td><span className="badge tf">{tf}</span></td>
                    <td>{!on ? "desligado" : c ? `candle ${fmtCandle(c.candle_time)}` : "aguardando"}</td>
                    <td title={sig.map(([k, v]) => `${SIGNAL_STATUS[k]?.label ?? k}: ${v}`).join(" · ")}>
                      {c ? `${c.analyzed} pares · ${sig.reduce((x, [, v]) => x + v, 0)} sinais · ${c.opportunities} oport. · ${c.executed.length} exec.` : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {Object.values(ov.last_cycles).some((c) => c?.risk_paused) && (
            <div className="sub neg">Pausado: {Object.values(ov.last_cycles).find((c) => c?.risk_paused)?.risk_paused}</div>
          )}
          <button className="btn small" style={{ marginTop: 8 }} disabled={running} onClick={runNow}>
            {running ? "Rodando..." : "Reprocessar últimos candles"}
          </button>
        </div>
        <BrokerCards snap={broker.snap} res={broker.res} />
        <div className="card clickable" onClick={() => goTo("research")}>
          <h3>Pesquisa (backtest)</h3>
          {TIMEFRAMES.map((tf) => {
            const r = ov.research[tf];
            return (
              <div key={tf} className="sub">
                <span className="badge tf">{tf}</span>{" "}
                {!r ? "nunca rodou" : r.status === "running" ? r.progress : r.status === "done" ? `concluída ${fmtDate(r.finished_at)}` : `erro`}
              </div>
            );
          })}
        </div>
      </div>

      <h2 className="section">Corretora BingX — posições abertas ({broker.snap?.positions?.length ?? "…"})</h2>
      <BrokerPositions snap={broker.snap} reload={broker.reload} onOpenTrade={setTrade} />

      <h2 className="section">Trades do bot em aberto ({ov.open_trades.length})</h2>
      {ov.open_trades.length === 0 ? <Empty>Nenhum trade aberto pelo motor.</Empty> : (
        <div className="trade-cards">
          {ov.open_trades.map((t) => (
            <div key={t.id} className="card trade-card clickable" onClick={() => setTrade(t.id)}>
              <div className="row between"><span className="row"><b>{t.symbol}</b><Dir d={t.direction} /><span className="badge tf">{t.timeframe}</span></span>
                <span className={`big ${cls(t.position?.unrealized_r)}`}>{fmtR(t.position?.unrealized_r)}</span></div>
              <div className="row"><span className="badge blue">{t.strategy_primary}</span>
                {t.strategies_supporting.map((s) => <span key={s} className="badge">{s}</span>)}<Level l={t.confluence_level} /></div>
              <div className="kv-line">
                <span>entrada {fmt(t.entry_price, 6)}</span><span>stop {fmt(t.current_stop, 6)}{t.breakeven_set && " (BE)"}</span>
                <span>alvo {fmt(t.target_reference, 6)}</span><span>PnL {fmt(t.position?.unrealized_pnl, 3)}</span>
              </div>
              <div className="muted small">{t.summary}</div>
              <div className="muted small">aberto {fmtDate(t.entry_time)} · score {fmt(t.final_score, 1)} · <Regime r={t.btc_regime} /></div>
            </div>
          ))}
        </div>
      )}

      <h2 className="section row between">
        <span>Estratégias</span>
        <button className="btn small" onClick={() => goTo("strategies")}>ver detalhes</button>
      </h2>
      <StrategyGroups st={st} onToggle={toggle} onOpen={setStrategy} />

      <h2 className="section row between">
        <span>Melhores oportunidades (últimas 2 horas)</span>
        <button className="btn small" onClick={() => goTo("opportunities")}>ver todas</button>
      </h2>
      {ov.top_opportunities.length === 0 ? <Empty>Nenhuma oportunidade nas últimas 2 horas (nenhum sinal aceito pelos filtros das estratégias).</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>TF</th><th>Candle</th><th>Par</th><th>Direção</th><th>Score</th><th>Confluência</th><th>Estratégias</th><th>Decisão</th></tr></thead>
            <tbody>
              {ov.top_opportunities.map((o) => (
                <tr key={o.id}>
                  <td><span className="badge tf">{o.timeframe}</span></td><td>{fmtCandle(o.candle_time)}</td><td><b>{o.symbol}</b></td><td><Dir d={o.direction} /></td><td>{fmt(o.final_score, 1)}</td>
                  <td><Level l={o.confluence_level} /></td>
                  <td>{[o.primary_strategy, ...o.supporting_strategies].join(", ")}</td>
                  <td className="wrap">{o.status} — {o.decision_reasons.join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="section">Trades recentes</h2>
      {ov.recent_trades.length === 0 ? <Empty>Nenhum trade fechado ainda.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Fechado</th><th>TF</th><th>Par</th><th>Direção</th><th>Estratégias</th><th>Saída</th><th>R</th><th>PnL</th><th>Duração</th></tr></thead>
            <tbody>
              {ov.recent_trades.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => setTrade(t.id)}>
                  <td>{fmtDate(t.exit_time)}</td><td><span className="badge tf">{t.timeframe}</span></td><td><b>{t.symbol}</b></td><td><Dir d={t.direction} /></td>
                  <td>{t.strategy_primary}{t.strategies_supporting.length ? ` + ${t.strategies_supporting.join(", ")}` : ""}</td>
                  <td>{t.exit_reason ? EXIT_LABEL[t.exit_reason] ?? t.exit_reason : t.status}</td>
                  <td className={cls(t.pnl_r)}>{fmtR(t.pnl_r)}</td>
                  <td className={cls(t.pnl)}>{fmt(t.pnl, 4)}{!t.pnl_confirmed && t.status === "CLOSED" && " *"}</td>
                  <td>{fmtDuration(t.duration_minutes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Help>* PnL ainda não confirmado pela corretora. Desempenho detalhado (LONG vs SHORT, combinações e regimes) na aba Desempenho.</Help>

      {trade !== null && <TradeDetail id={trade} onClose={() => setTrade(null)} onChanged={load} />}
      {strategy && <StrategyDetail sid={strategy} onClose={() => setStrategy(null)} onChanged={load} regimes={st.regimes} />}
    </>
  );
}
