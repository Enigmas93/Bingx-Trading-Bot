"use client";

import { fmt } from "@/lib/api";
import { FAMILY_LABEL, StrategiesResponse, StrategyInfo, TIMEFRAMES, cls, fmtR } from "@/lib/quant";
import { Health, Tag, Toggle } from "./ui";

/** Uma estratégia = um card; dentro, uma linha por timeframe (1h, 15m, 5m), cada uma com seu liga/desliga. */
export function groupStrategies(list: StrategyInfo[]) {
  const groups: { base: string; items: StrategyInfo[] }[] = [];
  for (const s of list) {
    const g = groups.find((x) => x.base === s.base_id);
    if (g) g.items.push(s);
    else groups.push({ base: s.base_id, items: [s] });
  }
  for (const g of groups) g.items.sort((a, b) => TIMEFRAMES.indexOf(a.timeframe as never) - TIMEFRAMES.indexOf(b.timeframe as never));
  return groups;
}

export default function StrategyGroups({ st, onToggle, onOpen, full }: {
  st: StrategiesResponse; onToggle: (sid: string, on: boolean) => void; onOpen: (sid: string) => void; full?: boolean;
}) {
  return (
    <div className={`strategy-grid ${full ? "big" : ""}`}>
      {groupStrategies(st.strategies).map(({ base, items }) => {
        const head = items[0];
        const anyOn = items.some((s) => s.settings?.enabled);
        return (
          <div key={base} className={`card strat-card ${anyOn ? "" : "off"}`}>
            <div className="row between">
              <span className="clickable" onClick={() => onOpen(head.strategy_id)}><b>{head.name}</b> <span className="muted">v{head.version}</span></span>
              <span className="badge">{FAMILY_LABEL[head.family] ?? head.family}</span>
            </div>
            {full && <div className="muted small clamp3">{head.description}</div>}
            {head.role === "filter" ? (
              <div className="row between">
                <span className="muted small">Filtro de contexto (regime do BTC 1h): não gera sinais, bloqueia direções contra o BTC.</span>
                {head.settings && <Toggle on={head.settings.enabled} onChange={(v) => onToggle(head.strategy_id, v)} />}
              </div>
            ) : (
              <table className="mini tf-table">
                <thead><tr><th>TF</th><th>Ativa</th><th>Saúde</th><th>DEMO</th><th>Backtest</th>{full && <th>LONG / SHORT (bt)</th>}<th></th></tr></thead>
                <tbody>
                  {items.map((s) => {
                    const set = s.settings;
                    const demo = s.performance.DEMO?.primary?.ALL;
                    const hist = s.performance.HISTORICAL?.primary?.ALL;
                    const hl = s.performance.HISTORICAL?.primary?.LONG;
                    const hs = s.performance.HISTORICAL?.primary?.SHORT;
                    return (
                      <tr key={s.strategy_id} className={set?.enabled ? "" : "dim"}>
                        <td><span className="badge tf">{s.timeframe}</span></td>
                        <td>{set && <Toggle on={set.enabled} onChange={(v) => onToggle(s.strategy_id, v)} />}</td>
                        <td>{set && <Health h={set.health} reason={set.health_reason} />}
                          {full && (set?.discovery_tags ?? []).map((t) => <Tag key={t} t={t} help={st.discovery_help[t]} />)}
                          {s.blocked_reason && <span className="badge red" title={s.blocked_reason}>bloqueada</span>}</td>
                        <td>{demo?.executions ? <span className={cls(demo.expectancy)}>{demo.executions} · {fmtR(demo.expectancy)}</span> : <span className="muted">-</span>}</td>
                        <td>{hist?.executions ? <span className={cls(hist.expectancy)} title={`PF ${fmt(hist.profit_factor, 2)}`}>{hist.executions} · {fmtR(hist.expectancy)}</span> : <span className="muted">-</span>}</td>
                        {full && <td><span className={cls(hl?.expectancy)}>{fmtR(hl?.expectancy)}</span> / <span className={cls(hs?.expectancy)}>{fmtR(hs?.expectancy)}</span></td>}
                        <td><button className="btn small" onClick={() => onOpen(s.strategy_id)}>detalhes</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
    </div>
  );
}
