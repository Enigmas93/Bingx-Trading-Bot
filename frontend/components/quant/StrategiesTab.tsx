"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { StrategiesResponse } from "@/lib/quant";
import { Health, Help, Tag } from "./ui";
import StrategyGroups from "./StrategyGroups";
import StrategyDetail from "./StrategyDetail";

export default function StrategiesTab() {
  const [st, setSt] = useState<StrategiesResponse | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => api<StrategiesResponse>("/quant/strategies").then(setSt), []);
  useEffect(() => { load(); }, [load]);

  const toggle = async (sid: string, enabled: boolean) => {
    await api(`/quant/strategies/${sid}`, { method: "PUT", body: JSON.stringify({ enabled }) });
    load();
  };

  if (!st) return <div className="muted">Carregando...</div>;
  return (
    <>
      <Help>
        Cada estratégia é uma hipótese independente, com regras próprias para LONG e SHORT, e roda em 3 timeframes
        (1h, 15m e 5m). Cada timeframe é uma variante separada: liga/desliga, desempenho e saúde próprios — dá para
        manter a estratégia no 1h e desligar só no 5m, por exemplo. Desativar só impede novos sinais: o histórico continua guardado. Clique no nome para ver regras, desempenho por escopo,
        walk-forward, regimes, sinais e trades.
      </Help>
      <StrategyGroups st={st} onToggle={toggle} onOpen={setOpen} full />
      <h2 className="section">Legenda</h2>
      <div className="two-col">
        <div className="card">
          <h3>Saúde (informativa; AUTO_DISABLE desligado por padrão)</h3>
          {Object.entries(st.health_help).map(([k, v]) => <div key={k}><Health h={k} /> {v}</div>)}
          <div className="muted small">Uma estratégia não é desativada só por perder alguns trades: a avaliação exige amostra mínima.</div>
        </div>
        <div className="card">
          <h3>Classificação pela pesquisa</h3>
          {Object.entries(st.discovery_help).map(([k, v]) => <div key={k}><Tag t={k} /> <span className="small">{v}</span></div>)}
        </div>
      </div>
      {open && <StrategyDetail sid={open} onClose={() => setOpen(null)} onChanged={load} regimes={st.regimes} />}
    </>
  );
}
