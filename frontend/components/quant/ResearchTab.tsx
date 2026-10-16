"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmt, fmtDate } from "@/lib/api";
import { LEVEL_LABEL, Metrics, REGIME_SHORT, TIMEFRAMES, TF_LABEL, cls, fmtR } from "@/lib/quant";
import { Empty, Help, Tag } from "./ui";

type Run = {
  id: number; timeframe: string; status: string; created_at: string; finished_at: string | null; progress: string | null; error: string | null;
  params: Record<string, unknown>; strategy_versions: Record<string, string>;
  summary: { symbols: number; trades: number; engine_trades: number; period: { start: number; end: number } } | null;
  results: {
    per_strategy: Record<string, { all: Metrics; LONG: Metrics; SHORT: Metrics; with_current_filters: Metrics; walk_forward: { positive_fraction: number | null; folds: Metrics[] } }>;
    engine: { all: Metrics; LONG: Metrics; SHORT: Metrics; by_level: Record<string, Metrics>; by_regime: Record<string, Metrics>; folds: Metrics[];
      combinations: Record<string, Metrics>; top_days_dependency: { total_r: number; days: number; without_top5_days_r: number; without_top10_days_r: number } };
    selection_walk_forward: { fold: number; selected: string[]; oos: Metrics }[];
    discovery: Record<string, string[]>;
    caveats: string[];
    symbols: string[];
  } | null;
} | null;

function MRow({ label, m }: { label: React.ReactNode; m?: Metrics }) {
  return (
    <tr><td>{label}</td><td>{m?.trades ?? 0}</td><td>{m?.win_rate != null ? `${fmt(m.win_rate, 1)}%` : "-"}</td>
      <td>{fmt(m?.profit_factor, 2)}</td><td className={cls(m?.expectancy)}>{fmtR(m?.expectancy)}</td>
      <td className={cls(m?.total_r)}>{fmtR(m?.total_r, 1)}</td><td>{fmt(m?.max_drawdown, 1)}R</td></tr>
  );
}
const MHead = ({ first }: { first: string }) => (
  <thead><tr><th>{first}</th><th>Trades</th><th>Acerto</th><th>PF</th><th>Expectativa</th><th>Total R</th><th>Máx. DD</th></tr></thead>
);

export default function ResearchTab() {
  const [run, setRun] = useState<Run>(null);
  const [tf, setTf] = useState("1h");
  const [days, setDays] = useState(120);
  const [topN, setTopN] = useState(30);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => api<Run>(`/quant/research?timeframe=${tf}`).then(setRun), [tf]);
  useEffect(() => { setDays(({ "1h": 120, "15m": 60, "5m": 30 } as Record<string, number>)[tf] ?? 60); }, [tf]);
  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  const start = async (all: boolean) => {
    setErr(null);
    try {
      await api("/quant/research", { method: "POST", body: JSON.stringify(all ? { top_n: topN } : { days, top_n: topN, timeframes: [tf] }) });
      load();
    } catch (e) { setErr((e as Error).message); }
  };

  const r = run?.results;
  return (
    <>
      <Help>Backtest + walk-forward de TODAS as estratégias com os dados da conta real (somente leitura) e as mesmas
        funções usadas ao vivo. Custos simulados: taxa 0,05% + slippage 0,05% por lado, funding estimado e stop com gap.
        Os parâmetros não são otimizados: o walk-forward mede se o resultado se mantém em janelas diferentes. Roda
        automaticamente todo domingo, para cada timeframe (1h: 120 dias, 15m: 60 dias, 5m: 30 dias). Nos timeframes
        curtos as taxas pesam muito mais: o filtro de custo descarta stops menores que 4× o custo da operação.</Help>
      <div className="row" style={{ marginBottom: 10 }}>
        {TIMEFRAMES.map((t) => <button key={t} className={`btn ${tf === t ? "primary" : ""}`} onClick={() => setTf(t)}>{TF_LABEL[t]}</button>)}
      </div>
      <div className="row">
        <div className="field"><label>Dias ({tf})</label><input type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} /></div>
        <div className="field"><label>Pares (top por volume)</label><input type="number" value={topN} onChange={(e) => setTopN(Number(e.target.value))} /></div>
        <button className="btn primary" disabled={run?.status === "running"} onClick={() => start(false)}>
          {run?.status === "running" ? "Rodando..." : `Rodar pesquisa ${tf}`}</button>
        <button className="btn" disabled={run?.status === "running"} onClick={() => start(true)}>Rodar os 3 timeframes</button>
      </div>
      {err && <div className="alert error">{err}</div>}
      {!run ? <Empty>Nenhuma pesquisa ainda.</Empty> : (
        <>
          <div className="muted small" style={{ margin: "8px 0" }}>
            Pesquisa #{run.id} · {run.status} {run.progress && `· ${run.progress}`} · iniciada {fmtDate(run.created_at)}
            {run.finished_at && ` · concluída ${fmtDate(run.finished_at)}`}
            {run.summary && ` · ${run.summary.symbols} pares · ${new Date(run.summary.period.start).toLocaleDateString("pt-BR")} a ${new Date(run.summary.period.end).toLocaleDateString("pt-BR")}`}
          </div>
          {run.error && <div className="alert error">{run.error}</div>}
          {r && (
            <>
              <h2 className="section">Cada estratégia isolada</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Estratégia</th><th>Classificação</th><th>Trades</th><th>Acerto</th><th>PF</th><th>Expectativa</th><th>Total R</th><th>Máx. DD</th>
                    <th>LONG</th><th>SHORT</th><th>Com filtros atuais</th><th>Walk-forward</th></tr></thead>
                  <tbody>
                    {Object.entries(r.per_strategy).sort((a, b) => (b[1].all.expectancy ?? -9) - (a[1].all.expectancy ?? -9)).map(([sid, p]) => (
                      <tr key={sid}>
                        <td><b>{sid}</b> <span className="muted">v{run.strategy_versions[sid]}</span></td>
                        <td>{(r.discovery[sid] ?? []).map((t) => <Tag key={t} t={t} />)}</td>
                        <td>{p.all.trades}</td><td>{p.all.win_rate != null ? `${fmt(p.all.win_rate, 1)}%` : "-"}</td>
                        <td>{fmt(p.all.profit_factor, 2)}</td><td className={cls(p.all.expectancy)}>{fmtR(p.all.expectancy)}</td>
                        <td className={cls(p.all.total_r)}>{fmtR(p.all.total_r, 1)}</td><td>{fmt(p.all.max_drawdown, 1)}R</td>
                        <td className={cls(p.LONG.expectancy)}>{fmtR(p.LONG.expectancy)} ({p.LONG.trades})</td>
                        <td className={cls(p.SHORT.expectancy)}>{fmtR(p.SHORT.expectancy)} ({p.SHORT.trades})</td>
                        <td className={cls(p.with_current_filters.expectancy)}>{fmtR(p.with_current_filters.expectancy)} ({p.with_current_filters.trades})</td>
                        <td>{p.walk_forward.folds.map((f, i) => <span key={i} className={`fold ${cls(f.expectancy)}`} title={`${f.trades} trades`}>{fmtR(f.expectancy, 2)}</span>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <h2 className="section">Motor completo (filtros + confluência + score atuais)</h2>
              <div className="two-col">
                <div className="table-wrap"><table><MHead first="Recorte" /><tbody>
                  <MRow label={<b>Total</b>} m={r.engine.all} /><MRow label="LONG" m={r.engine.LONG} /><MRow label="SHORT" m={r.engine.SHORT} />
                  {Object.entries(r.engine.by_level).map(([k, m]) => <MRow key={k} label={LEVEL_LABEL[k]} m={m} />)}
                  {r.engine.folds.map((m, i) => <MRow key={i} label={`Janela ${i + 1}`} m={m} />)}
                </tbody></table></div>
                <div className="table-wrap"><table><MHead first="Regime BTC" /><tbody>
                  {Object.entries(r.engine.by_regime).map(([k, m]) => <MRow key={k} label={REGIME_SHORT[k] ?? k} m={m} />)}
                </tbody></table></div>
              </div>
              <div className="card" style={{ marginTop: 12 }}>
                <h3>Dependência de poucos dias</h3>
                Total {fmtR(r.engine.top_days_dependency.total_r, 1)} em {r.engine.top_days_dependency.days} dias ·
                sem os 5 melhores dias: <b className={cls(r.engine.top_days_dependency.without_top5_days_r)}>{fmtR(r.engine.top_days_dependency.without_top5_days_r, 1)}</b> ·
                sem os 10 melhores: <b className={cls(r.engine.top_days_dependency.without_top10_days_r)}>{fmtR(r.engine.top_days_dependency.without_top10_days_r, 1)}</b>
              </div>

              <h2 className="section">Walk-forward de seleção (fora da amostra)</h2>
              <div className="table-wrap"><table>
                <thead><tr><th>Janela testada</th><th>Selecionadas pelas janelas anteriores</th><th>Trades</th><th>Expectativa</th><th>Total R</th></tr></thead>
                <tbody>{r.selection_walk_forward.map((s) => (
                  <tr key={s.fold}><td>{s.fold}ª</td><td className="wrap">{s.selected.join(", ") || "nenhuma"}</td><td>{s.oos.trades}</td>
                    <td className={cls(s.oos.expectancy)}>{fmtR(s.oos.expectancy)}</td><td className={cls(s.oos.total_r)}>{fmtR(s.oos.total_r, 1)}</td></tr>
                ))}</tbody>
              </table></div>
              <Help>Pergunta respondida: &quot;se eu escolhesse as estratégias que estavam positivas até aqui, o que teria acontecido depois?&quot;</Help>

              <h2 className="section">Combinações no motor</h2>
              {Object.keys(r.engine.combinations).length === 0 ? <Empty>Nenhuma combinação com trades.</Empty> : (
                <div className="table-wrap"><table><MHead first="Combinação" /><tbody>
                  {Object.entries(r.engine.combinations).map(([k, m]) => <MRow key={k} label={k} m={m} />)}
                </tbody></table></div>
              )}

              <h2 className="section">Limitações</h2>
              <ul>{r.caveats.map((c, i) => <li key={i} className="muted">{c}</li>)}</ul>
              <div className="muted small">Pares: {r.symbols.join(", ")}</div>
            </>
          )}
        </>
      )}
    </>
  );
}
