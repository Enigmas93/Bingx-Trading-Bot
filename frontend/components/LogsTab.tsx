"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, LogEntry } from "@/lib/api";

const MODULES = ["", "scanner", "auto_execute", "executor", "news", "sync", "scheduler", "api"];

export default function LogsTab() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [module, setModule] = useState("");

  const load = useCallback(async () => {
    const qs = module ? `&module=${module}` : "";
    setLogs(await api<LogEntry[]>(`/logs?limit=300${qs}`));
  }, [module]);

  useEffect(() => {
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        {MODULES.map((m) => (
          <button key={m} className={`btn small ${module === m ? "primary" : ""}`} onClick={() => setModule(m)}>
            {m || "todos"}
          </button>
        ))}
      </div>
      <div className="table-wrap">
        {logs.length === 0 && <div className="log-line muted">Sem logs.</div>}
        {logs.map((l) => (
          <div className="log-line" key={l.id}>
            <span className="muted">{fmtDate(l.created_at)}</span> <span className={`lvl-${l.level}`}>{l.level}</span>{" "}
            <b>[{l.module}]</b> {l.message}
          </div>
        ))}
      </div>
    </>
  );
}
