"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { COMPONENT_LABEL, DataInfo, LEVEL_LABEL, QuantConfig, Safety, TIMEFRAMES, TF_LABEL } from "@/lib/quant";
import { Help, Toggle } from "./ui";
import GeneralSettings from "./GeneralSettings";

export default function EngineTab() {
  const [safety, setSafety] = useState<(Safety & DataInfo) | null>(null);
  const [cfg, setCfg] = useState<QuantConfig | null>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);

  useEffect(() => {
    api<Safety & DataInfo>("/quant/safety").then(setSafety);
    api<QuantConfig>("/quant/config").then(setCfg);
  }, []);

  const save = async (patch?: Partial<QuantConfig>) => {
    if (!cfg) return;
    // só campos editáveis (id/updated_at são do servidor e seriam recusados)
    // campos controlados pelo servidor não são enviados
    const READ_ONLY = ["id", "updated_at", "risk_reset_at", "block_timezone"];
    const editable = Object.fromEntries(Object.entries(cfg).filter(([k]) => !READ_ONLY.includes(k)));
    const body = patch ?? editable;
    try {
      setCfg(await api<QuantConfig>("/quant/config", { method: "PUT", body: JSON.stringify(body) }));
      setMsg({ kind: "ok", text: "Configuração do motor salva." });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
    }
  };

  const num = (k: keyof QuantConfig, label: string, hint: string, step = "1") => (
    <div className="field"><label>{label}</label>
      <input type="number" step={step} value={cfg ? (cfg[k] as number) : 0}
        onChange={(e) => cfg && setCfg({ ...cfg, [k]: Number(e.target.value) })} />
      <div className="hint">{hint}</div></div>
  );
  const flag = (k: keyof QuantConfig, label: string, hint: string) => (
    <div className="field"><label>{label}</label>
      <Toggle on={!!cfg?.[k]} onChange={(v) => cfg && setCfg({ ...cfg, [k]: v })} label={cfg?.[k] ? "ligado" : "desligado"} />
      <div className="hint">{hint}</div></div>
  );

  return (
    <>
      <h2 className="section">Modo de execução</h2>
      {safety && (
        <div className={`mode-screen ${safety.safety_ok ? "" : "locked"}`}>
          <div className="mode-big">DEMO ONLY</div>
          <div className="mode-big red">LIVE EXECUTION: BLOCKED</div>
          <div className="mode-grid">
            <div><span>Análise (dados)</span>{safety.analysis_data.name} — {safety.analysis_data.endpoint} ({safety.analysis_data.access})</div>
            <div><span>Execução (ordens)</span>{safety.execution.name} — {safety.execution.endpoint}</div>
            <div><span>DEMO_MODE</span>{String(safety.demo_mode)}</div>
            <div><span>LIVE_TRADING</span>{String(safety.live_trading)}</div>
            <div><span>Estado do motor</span>{safety.engine_state === "READY" ? "PRONTO (somente DEMO)" : "TRAVADO"}</div>
          </div>
          {!safety.safety_ok && (
            <div className="alert error" style={{ marginTop: 10 }}>
              ABORT EXECUTION · LIVE_CONFIGURATION_DETECTED — nenhuma ordem será enviada: {safety.reasons.join("; ")}
            </div>
          )}
          <Help>Esta versão é exclusivamente de validação. Não existe opção na interface para operar na conta real.
            Toda a análise já usa os dados da conta real; ao migrar no futuro, somente o adaptador de execução muda.
            Travas em três camadas: configuração (.env), verificação do motor e o próprio cliente BingX.</Help>
        </div>
      )}

      <h2 className="section">Motor de decisão</h2>
      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}
      {cfg && (
        <>
          <div className="form-grid">
            <div className="field"><label>Execução automática na DEMO</label>
              <Toggle on={cfg.engine_enabled} onChange={(v) => save({ engine_enabled: v })} label={cfg.engine_enabled ? "ligada" : "modo observação"} />
              <div className="hint">Desligada: o motor continua analisando e registrando, mas não abre trades.</div></div>
            <div className="field"><label>Pausa diária de novas entradas</label>
              <div className="row">
                <Toggle on={cfg.block_hours_enabled} onChange={(v) => save({ block_hours_enabled: v })} label={cfg.block_hours_enabled ? "ligada" : "desligada"} />
                <select className="select" value={cfg.block_start_hour} onChange={(e) => save({ block_start_hour: Number(e.target.value) })}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                </select>
                <span>até</span>
                <select className="select" value={cfg.block_end_hour} onChange={(e) => save({ block_end_hour: Number(e.target.value) })}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                </select>
              </div>
              <div className="hint">todos os dias, horário de Brasília. Só bloqueia NOVAS entradas: posições abertas seguem protegidas e gerenciadas.</div></div>
            <div className="field"><label>Timeframes analisados</label>
              <div className="row">
                {TIMEFRAMES.map((t) => {
                  const cur: string[] = cfg.timeframes ?? [...TIMEFRAMES];
                  return <Toggle key={t} on={cur.includes(t)} label={TF_LABEL[t]} onChange={(v) => {
                    const next = v ? [...cur, t] : cur.filter((x) => x !== t);
                    if (next.length) save({ timeframes: TIMEFRAMES.filter((x) => next.includes(x)) });
                  }} />;
                })}
              </div>
              <div className="hint">desligar um timeframe pausa todas as variantes dele (cada uma também pode ser desligada na aba Estratégias)</div></div>
            {num("min_final_score", "Score final mínimo (0-100)", "oportunidades abaixo disso são descartadas")}
            <div className="field"><label>Confluência mínima</label>
              <select value={cfg.min_confluence_level} onChange={(e) => setCfg({ ...cfg, min_confluence_level: e.target.value })}>
                {Object.entries(LEVEL_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <div className="hint">número mínimo de estratégias concordando</div></div>
            {num("single_signal_min_score", "Sinal único: score mínimo", "exceção para sinal de uma estratégia só")}
            {num("conflict_margin", "Margem de conflito LONG×SHORT", "se ambas as direções aparecem, a diferença mínima de score")}
            {num("max_open_per_strategy", "Máx. posições por estratégia", "limite de trades abertos liderados pela mesma estratégia")}
            {num("max_correlation", "Correlação máxima", "evita dois trades muito correlacionados na mesma direção", "0.05")}
            {num("breakeven_at_r", "Breakeven ao atingir (R)", "0 desliga; o stop vai para a entrada + taxas", "0.1")}
            {flag("exit_on_regime_change", "Sair na mudança de regime", "fecha se o BTC virar contra a direção")}
            {flag("exit_on_momentum_loss", "Sair na perda de momentum", "estratégias de tendência: EMA21 + MACD contra")}
            <div className="field"><label>Perda de momentum vale em</label>
              <div className="row">
                {TIMEFRAMES.map((t) => {
                  const cur: string[] = cfg.momentum_exit_timeframes ?? [...TIMEFRAMES];
                  return <Toggle key={t} on={cur.includes(t)} label={t} onChange={(v) =>
                    save({ momentum_exit_timeframes: TIMEFRAMES.filter((x) => (v ? [...cur, t] : cur.filter((y) => y !== t)).includes(x)) })} />;
                })}
              </div>
              <div className="hint">DEMO até 16/10: no 1h a saída ajudou; no 15m/5m segurar até stop/alvo teria sido melhor</div></div>
            {flag("exit_on_opposite_signal", "Sair com sinal oposto", "outra estratégia aceita na direção contrária")}
            {flag("auto_disable", "AUTO_DISABLE", "desligado (padrão): saúde só informa; ligado: desativa estratégia DEGRADED com amostra grande")}
            {num("health_min_trades", "Amostra mínima para saúde", "trades antes de avaliar a saúde")}
            {num("research_days", "Pesquisa 1h: dias", "período do backtest automático")}
            {num("research_days_15m", "Pesquisa 15m: dias", "período do backtest automático")}
            {num("research_days_5m", "Pesquisa 5m: dias", "período do backtest automático")}
            {num("research_top_n", "Pesquisa: pares", "top por volume na conta real")}
          </div>
          <h4>Pesos do score (normalizados automaticamente)</h4>
          <div className="form-grid">
            {Object.entries(cfg.weights).map(([k, v]) => (
              <div key={k} className="field"><label>{COMPONENT_LABEL[k] ?? k}</label>
                <input type="number" step="0.01" value={v} onChange={(e) => setCfg({ ...cfg, weights: { ...cfg.weights, [k]: Number(e.target.value) } })} /></div>
            ))}
          </div>
          <button className="btn primary" style={{ marginTop: 14 }} onClick={() => save()}>Salvar</button>
        </>
      )}

      <h2 className="section">Configurações gerais (posições, tamanho, risco e filtros)</h2>
      <GeneralSettings />
    </>
  );
}
