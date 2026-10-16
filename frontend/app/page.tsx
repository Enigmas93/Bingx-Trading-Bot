"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Status } from "@/lib/api";
import { DataInfo, Safety } from "@/lib/quant";
import LogsTab from "@/components/LogsTab";
import Dashboard from "@/components/quant/Dashboard";
import OpportunitiesTab from "@/components/quant/OpportunitiesTab";
import StrategiesTab from "@/components/quant/StrategiesTab";
import JournalTab from "@/components/quant/JournalTab";
import QuantPerformanceTab from "@/components/quant/QuantPerformanceTab";
import ResearchTab from "@/components/quant/ResearchTab";
import ReportsTab from "@/components/quant/ReportsTab";
import EventsTab from "@/components/quant/EventsTab";
import EngineTab from "@/components/quant/EngineTab";

const TABS = [
  { id: "dashboard", label: "Painel" },
  { id: "opportunities", label: "Oportunidades" },
  { id: "strategies", label: "Estratégias" },
  { id: "journal", label: "Diário de trades" },
  { id: "qperformance", label: "Desempenho" },
  { id: "research", label: "Pesquisa" },
  { id: "reports", label: "Relatórios" },
  { id: "events", label: "Eventos" },
  { id: "engine", label: "Motor & Segurança" },
  { id: "logs", label: "Logs" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function ModeBanner({ safety }: { safety: (Safety & DataInfo) | null }) {
  if (!safety) return null;
  if (!safety.safety_ok) {
    return (
      <div className="mode-banner locked">
        <b>ABORT EXECUTION · LIVE_CONFIGURATION_DETECTED</b> — motor travado, nenhuma ordem será enviada: {safety.reasons.join("; ")}
      </div>
    );
  }
  return (
    <div className="mode-banner">
      <span><b>SOMENTE DEMO</b> · EXECUÇÃO REAL BLOQUEADA</span>
      <span className="muted">Análise: dados da conta REAL (somente leitura) · Ordens: conta DEMO (VST)</span>
    </div>
  );
}

export default function Home() {
  const [tab, setTab] = useState<TabId>("dashboard");
  const [status, setStatus] = useState<Status | null>(null);
  const [safety, setSafety] = useState<(Safety & DataInfo) | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const [s, q] = await Promise.all([api<Status>("/status"), api<Safety & DataInfo>("/quant/safety")]);
      setStatus(s);
      setSafety(q);
      setError(null);
    } catch (e) {
      setError(`Backend indisponível: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    refreshStatus();
    const id = setInterval(refreshStatus, 15000);
    return () => clearInterval(id);
  }, [refreshStatus]);

  const b = status?.balance;
  return (
    <div className="container">
      <header className="top">
        <h1><span>BingX</span> Quant Bot</h1>
        {status && (
          <div className="row">
            <span className="badge blue">Saldo DEMO {b ? b.equity.toLocaleString("pt-BR", { maximumFractionDigits: 2 }) : "-"} {b?.asset ?? ""}</span>
            <span className={`badge ${status.scheduler_running ? "green" : "red"}`}>
              Agendador {status.scheduler_running ? "ativo" : "parado"}
            </span>
            <button className="btn small" title="Sair do painel (só tem efeito quando a senha está ativa)"
              onClick={async () => { await fetch("/api/auth/logout", { method: "POST" }); window.location.href = "/login"; }}>Sair</button>
          </div>
        )}
      </header>
      <ModeBanner safety={safety} />

      {error && <div className="alert error">{error}</div>}

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
</nav>

      {tab === "dashboard" && <Dashboard goTo={(t) => setTab(t as TabId)} />}
      {tab === "opportunities" && <OpportunitiesTab />}
      {tab === "strategies" && <StrategiesTab />}
      {tab === "journal" && <JournalTab />}
      {tab === "qperformance" && <QuantPerformanceTab />}
      {tab === "research" && <ResearchTab />}
      {tab === "reports" && <ReportsTab />}
      {tab === "events" && <EventsTab />}
      {tab === "engine" && <EngineTab />}
      {tab === "logs" && <LogsTab />}

    </div>
  );
}
