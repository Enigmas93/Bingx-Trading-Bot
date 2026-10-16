"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Help, Toggle } from "./ui";

// Configurações gerais (tabela do sistema) que o motor quant usa: limites de posição, tamanho, risco do dia,
// universo de pares, filtros de custo e notícias.
type G = Record<string, number | boolean>;

const NUM: [string, string, string, string?][] = [
  ["max_positions", "Máx. posições simultâneas", "todas as estratégias e timeframes somados"],
  ["max_same_direction", "Máx. posições na mesma direção", "LONG ou SHORT"],
  ["leverage", "Alavancagem", "aplicada em cada ordem na DEMO"],
  ["risk_per_trade_pct", "Risco por trade (% do saldo)", "perda até o stop", "0.1"],
  ["capital_percent", "Margem máx. por posição (% do saldo)", "teto de margem de cada trade", "0.5"],
  ["daily_loss_limit_pct", "Limite de perda diária (%)", "pausa novas entradas até 00:00 UTC", "0.5"],
  ["max_consecutive_losses", "Perdas seguidas para pausar", "0 desliga"],
  ["scan_top_n", "Pares analisados (top por volume)", "ranking da conta real"],
  ["min_volume_usdt", "Volume mínimo 24h (USDT)", "na conta real"],
  ["max_spread_pct", "Spread máximo (%)", "acima disso a oportunidade é descartada", "0.01"],
  ["max_funding_pct", "Funding máximo (%)", "em módulo", "0.01"],
  ["min_stop_cost_ratio", "Stop mínimo × custo", "o stop precisa valer N× (taxas de ida e volta + spread)", "0.5"],
  ["fee_rate_pct", "Taxa por lado (%)", "taker da BingX", "0.01"],
];
const BOOL: [string, string, string][] = [
  ["news_check_enabled", "Checar notícias antes de entrar", "NVIDIA + CoinMarketCal + BLS/BEA"],
  ["news_block_generic", "Bloquear por sentimento negativo genérico", "além das notícias específicas do par"],
];

export default function GeneralSettings() {
  const [g, setG] = useState<G | null>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  useEffect(() => { api<G>("/config").then(setG); }, []);

  const save = async () => {
    if (!g) return;
    const body = Object.fromEntries([...NUM.map(([k]) => [k, Number(g[k])]), ...BOOL.map(([k]) => [k, !!g[k]])]);
    try {
      setG(await api<G>("/config", { method: "PUT", body: JSON.stringify(body) }));
      setMsg({ kind: "ok", text: "Configurações gerais salvas." });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
    }
  };

  if (!g) return <div className="muted">Carregando...</div>;
  return (
    <>
      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}
      <div className="form-grid">
        {NUM.map(([k, label, hint, step]) => (
          <div key={k} className="field"><label>{label}</label>
            <input type="number" step={step ?? "1"} value={Number(g[k] ?? 0)} onChange={(e) => setG({ ...g, [k]: Number(e.target.value) })} />
            <div className="hint">{hint}</div></div>
        ))}
        {BOOL.map(([k, label, hint]) => (
          <div key={k} className="field"><label>{label}</label>
            <Toggle on={!!g[k]} onChange={(v) => setG({ ...g, [k]: v })} label={g[k] ? "ligado" : "desligado"} />
            <div className="hint">{hint}</div></div>
        ))}
      </div>
      <button className="btn primary" style={{ marginTop: 14 }} onClick={save}>Salvar configurações gerais</button>
      <Help>Tamanho da posição: o bot arrisca o "risco por trade" até o stop, limitado pela margem máxima por posição.</Help>
    </>
  );
}
