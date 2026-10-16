"use client";

import { useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import {
  FAMILY_LABEL, Metrics, PerfRow, REGIME_SHORT, SIGNAL_STATUS, StrategySettings, cls, fmtCandle, fmtR,
} from "@/lib/quant";
import { Dir, Empty, Health, Help, Modal, Regime, Tag, Toggle } from "./ui";
import TradeDetail from "./TradeDetail";

type Detail = {
  metadata: Record<string, any>;
  settings: StrategySettings;
  blocked_reason: string | null;
  versions: { version: string; created_at: string; definition: Record<string, any> }[];
  performance: Record<string, Record<string, Record<string, PerfRow>>>;
  signals: any[];
  trades: any[];
  research: {
    all: Metrics; LONG: Metrics; SHORT: Metrics; with_current_filters: Metrics;
    exit_reasons: Record<string, number>; by_regime: Record<string, Metrics>;
    walk_forward: { folds: Metrics[]; positive_fraction: number | null };
  } | null;
};

const PARAM_HELP: Record<string, string> = {
  min_slope: "inclinação mínima da EMA50 (%)", min_adx: "ADX mínimo", min_vol_ratio: "volume mínimo vs média",
  stop_atr: "stop em ATRs", target_r: "alvo em múltiplos do risco",
};

export default function StrategyDetail({ sid, onClose, onChanged, regimes }: { sid: string; onClose: () => void; onChanged?: () => void; regimes: string[] }) {
  const [d, setD] = useState<Detail | null>(null);
  const [form, setForm] = useState<Partial<StrategySettings>>({});
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [trade, setTrade] = useState<number | null>(null);

  const load = () => api<Detail>(`/quant/strategies/${sid}`).then((x) => { setD(x); setForm(x.settings); });
  useEffect(() => { load(); }, [sid]);

  const save = async (patch?: Partial<StrategySettings>) => {
    const body = patch ?? {
      enabled: form.enabled, min_score: Number(form.min_score), allow_long: form.allow_long, allow_short: form.allow_short,
      accepted_regimes: form.accepted_regimes, cooldown_minutes: Number(form.cooldown_minutes),
      max_trades_per_day: Number(form.max_trades_per_day),
    };
    try {
      await api(`/quant/strategies/${sid}`, { method: "PUT", body: JSON.stringify(body) });
      setMsg({ kind: "ok", text: "Configuração salva (o histórico da estratégia é preservado)." });
      await load();
      onChanged?.();
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
    }
  };

  if (!d) return <Modal onClose={onClose} title={sid}><div className="muted">Carregando...</div></Modal>;
  const m = d.metadata;
  const isFilter = m.role === "filter";
  const perf = d.performance;

  const perfRow = (scope: string, attr: string, dir: string) => perf[scope]?.[attr]?.[dir];

  return (
    <Modal wide onClose={onClose} title={<span className="row"><b>{m.name}</b><span className="muted">{m.strategy_id} v{m.version}</span>
      <span className="badge">{FAMILY_LABEL[m.family] ?? m.family}</span><Health h={d.settings.health} reason={d.settings.health_reason} />
      {d.settings.discovery_tags.map((t) => <Tag key={t} t={t} />)}</span>}>
      {d.blocked_reason && <div className="alert error">{d.blocked_reason}</div>}
      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}
      <p>{m.description}</p>
      <div className="muted small">Saúde: {d.settings.health_reason ?? "-"} · Timeframe {m.timeframe} · saída máxima {m.max_hold_bars} candles ·
        estilo de saída {m.exit_style === "trend" ? "tendência (sai na perda de momentum)" : m.exit_style === "reversion" ? "reversão (alvo/tempo)" : "-"}</div>

      <div className="two-col">
        <div>
          <h4>Configuração operacional</h4>
          <div className="form-grid tight">
            <div className="field"><label>Ativa</label><Toggle on={!!form.enabled} onChange={(v) => save({ enabled: v })} label={form.enabled ? "ligada" : "desligada"} /></div>
            {!isFilter && <>
              <div className="field"><label>Score mínimo do sinal (0-100)</label>
                <input type="number" value={form.min_score ?? 0} onChange={(e) => setForm({ ...form, min_score: Number(e.target.value) })} /></div>
              <div className="field"><label>Direções</label>
                <div className="row">
                  <Toggle on={!!form.allow_long} onChange={(v) => setForm({ ...form, allow_long: v })} label="LONG" />
                  <Toggle on={!!form.allow_short} onChange={(v) => setForm({ ...form, allow_short: v })} label="SHORT" />
                </div></div>
              <div className="field"><label>Cooldown no mesmo par (min)</label>
                <input type="number" value={form.cooldown_minutes ?? 0} onChange={(e) => setForm({ ...form, cooldown_minutes: Number(e.target.value) })} /></div>
              <div className="field"><label>Máx. trades por dia</label>
                <input type="number" value={form.max_trades_per_day ?? 0} onChange={(e) => setForm({ ...form, max_trades_per_day: Number(e.target.value) })} /></div>
            </>}
          </div>
          {!isFilter && <>
            <div className="field" style={{ marginTop: 10 }}><label>Regimes do BTC aceitos (hipótese — valide pela matriz de regime)</label>
              <div className="chips">
                {regimes.map((r) => {
                  const on = (form.accepted_regimes ?? []).includes(r);
                  return <span key={r} className={`chip ${on ? "on" : ""}`} onClick={() => setForm({
                    ...form, accepted_regimes: on ? (form.accepted_regimes ?? []).filter((x) => x !== r) : [...(form.accepted_regimes ?? []), r],
                  })}>{on ? "✓ " : ""}{REGIME_SHORT[r]}</span>;
                })}
              </div></div>
            <button className="btn primary" style={{ marginTop: 10 }} onClick={() => save()}>Salvar configuração</button>
          </>}
          <Help>Os parâmetros das regras (ao lado) fazem parte da versão. Para mudá-los é preciso criar uma nova versão no código;
            o histórico da versão anterior é mantido. O bot nunca altera parâmetros sozinho.</Help>
        </div>
        <div>
          <h4>Parâmetros da versão {m.version}</h4>
          <table className="mini">
            <thead><tr><th>Parâmetro</th><th>LONG</th><th>SHORT</th></tr></thead>
            <tbody>
              {Array.from(new Set([...Object.keys(m.long_params), ...Object.keys(m.short_params)])).map((k) => (
                <tr key={k}><td title={PARAM_HELP[k]}>{k}</td><td>{String(m.long_params[k] ?? "-")}</td><td>{String(m.short_params[k] ?? "-")}</td></tr>
              ))}
            </tbody>
          </table>
          <div className="muted small">Versões registradas: {d.versions.map((v) => `v${v.version} (${fmtDate(v.created_at)})`).join(", ")}</div>
        </div>
      </div>

      {!isFilter && <>
        <h4>Desempenho (atribuição primária) — escopos separados</h4>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Escopo</th><th>Direção</th><th>Trades</th><th>Acerto</th><th>PF</th><th>Expectativa</th><th>Total R</th><th>Máx. DD</th><th>PnL</th><th>Últimos 10</th></tr></thead>
            <tbody>
              {["DEMO", "RECENT", "HISTORICAL"].flatMap((sc) => ["ALL", "LONG", "SHORT"].map((dir) => {
                const r = perfRow(sc, "primary", dir);
                return (
                  <tr key={sc + dir}>
                    <td>{dir === "ALL" ? <b>{sc === "RECENT" ? "RECENT (backtest 30d)" : sc === "HISTORICAL" ? "HISTORICAL (backtest)" : "DEMO (real)"}</b> : ""}</td>
                    <td>{dir === "ALL" ? "Todas" : <Dir d={dir} />}</td>
                    <td>{r?.executions ?? 0}</td><td>{r?.win_rate != null ? `${fmt(r.win_rate, 1)}%` : "-"}</td>
                    <td>{fmt(r?.profit_factor, 2)}</td><td className={cls(r?.expectancy)}>{fmtR(r?.expectancy)}</td>
                    <td className={cls(r?.total_r)}>{fmtR(r?.total_r, 1)}</td><td>{fmt(r?.max_drawdown, 1)}R</td>
                    <td>{sc === "DEMO" ? fmt(r?.total_pnl, 2) : "-"}</td><td>{r?.last10 ?? "-"}</td>
                  </tr>
                );
              }))}
            </tbody>
          </table>
        </div>
        <Help>DEMO usa o PnL realizado na conta demo. HISTORICAL/RECENT são simulações com dados da conta real e custos
          estimados — não se misturam com o DEMO. Como participante (primária + apoio), a estratégia esteve em
          {" "}{perfRow("DEMO", "participant", "ALL")?.executions ?? 0} trades DEMO.</Help>

        {d.research && <>
          <h4>Pesquisa: walk-forward e regimes (backtest)</h4>
          <div className="two-col">
            <div>
              <table className="mini">
                <thead><tr><th>Janela</th><th>Trades</th><th>Expectativa</th><th>PF</th></tr></thead>
                <tbody>
                  {d.research.walk_forward.folds.map((f, i) => (
                    <tr key={i}><td>{i + 1}ª</td><td>{f.trades}</td><td className={cls(f.expectancy)}>{fmtR(f.expectancy)}</td><td>{fmt(f.profit_factor, 2)}</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="muted small">Positiva em {d.research.walk_forward.positive_fraction != null ? `${Math.round(d.research.walk_forward.positive_fraction * 100)}%` : "-"} das janelas
                (parâmetros fixos, sem otimização). Com os filtros atuais: {d.research.with_current_filters.trades} trades, {fmtR(d.research.with_current_filters.expectancy)}/trade.</div>
            </div>
            <div>
              <table className="mini">
                <thead><tr><th>Regime BTC</th><th>Trades</th><th>Expectativa</th><th>Acerto</th></tr></thead>
                <tbody>
                  {Object.entries(d.research.by_regime).map(([r, x]) => (
                    <tr key={r}><td><Regime r={r} /></td><td>{x.trades}</td><td className={cls(x.expectancy)}>{fmtR(x.expectancy)}</td><td>{fmt(x.win_rate, 1)}%</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="muted small">Saídas: {Object.entries(d.research.exit_reasons).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
            </div>
          </div>
        </>}

        <h4>Trades DEMO desta estratégia</h4>
        {d.trades.length === 0 ? <Empty>Nenhum trade ainda.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Entrada</th><th>Par</th><th>Direção</th><th>Papel</th><th>Status</th><th>R</th><th>PnL</th></tr></thead>
            <tbody>{d.trades.map((t) => (
              <tr key={t.id} className="clickable" onClick={() => setTrade(t.id)}>
                <td>{fmtDate(t.entry_time)}</td><td>{t.symbol}</td><td><Dir d={t.direction} /></td><td>{t.role}</td><td>{t.status}</td>
                <td className={cls(t.pnl_r)}>{fmtR(t.pnl_r)}</td><td className={cls(t.pnl)}>{fmt(t.pnl, 4)}</td>
              </tr>))}</tbody>
          </table></div>
        )}

        <h4>Últimos sinais</h4>
        {d.signals.length === 0 ? <Empty>Nenhum sinal registrado.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Candle</th><th>Par</th><th>Direção</th><th>Score</th><th>Regime</th><th>Status</th><th>Motivo</th></tr></thead>
            <tbody>{d.signals.map((s) => (
              <tr key={s.id}><td>{fmtCandle(s.candle_time)}</td><td>{s.symbol}</td><td><Dir d={s.direction} /></td><td>{fmt(s.score, 0)}</td>
                <td><Regime r={s.regime} /></td><td><span className={`badge ${SIGNAL_STATUS[s.status]?.color ?? ""}`} title={SIGNAL_STATUS[s.status]?.help}>{SIGNAL_STATUS[s.status]?.label ?? s.status}</span></td>
                <td className="wrap">{s.reason}</td></tr>))}</tbody>
          </table></div>
        )}
      </>}
      {trade !== null && <TradeDetail id={trade} onClose={() => setTrade(null)} />}
      <div className="muted small" style={{ marginTop: 10 }}>Classificações descrevem o que os dados mostraram; não são garantia de resultado.</div>
    </Modal>
  );
}
