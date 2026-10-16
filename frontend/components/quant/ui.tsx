"use client";

import { ReactNode, useEffect } from "react";
import { HEALTH_COLOR, HEALTH_LABEL, LEVEL_LABEL, REGIME_COLOR, REGIME_SHORT, TAG_COLOR, TAG_LABEL } from "@/lib/quant";

export function Dir({ d }: { d: string }) {
  return <span className={`badge ${d === "LONG" ? "green" : "red"}`}>{d === "LONG" ? "▲ LONG" : "▼ SHORT"}</span>;
}

export function Regime({ r, long }: { r: string | null | undefined; long?: string }) {
  if (!r) return <span className="muted">-</span>;
  return (
    <span className="badge regime" style={{ color: REGIME_COLOR[r], borderColor: REGIME_COLOR[r] }} title={r}>
      {long ?? REGIME_SHORT[r] ?? r}
    </span>
  );
}

export function Level({ l }: { l: string }) {
  const color = l === "STRONG_CONFLUENCE" ? "green" : l === "CONFLUENCE" ? "blue" : "";
  return <span className={`badge ${color}`}>{LEVEL_LABEL[l] ?? l}</span>;
}

export function Health({ h, reason }: { h: string; reason?: string | null }) {
  return <span className={`badge ${HEALTH_COLOR[h] ?? ""}`} title={reason ?? ""}>{HEALTH_LABEL[h] ?? h}</span>;
}

export function Tag({ t, help }: { t: string; help?: string }) {
  return <span className={`badge ${TAG_COLOR[t] ?? ""}`} title={help}>{TAG_LABEL[t] ?? t}</span>;
}

export function ScoreBar({ value, max = 100, color }: { value: number; max?: number; color?: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <span className="score-bar">
      <div style={{ width: `${pct}%`, background: color }} />
    </span>
  );
}

export function Toggle({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean }) {
  return (
    <span className={`toggle ${on ? "on" : ""} ${disabled ? "disabled" : ""}`} onClick={() => !disabled && onChange(!on)}>
      <span className="track" />
      {label && <span>{label}</span>}
    </span>
  );
}

export function Modal({ title, onClose, children, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className={`modal ${wide ? "wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button className="btn small" onClick={onClose}>Fechar ✕</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function Help({ children }: { children: ReactNode }) {
  return <div className="help">{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Conditions({ items }: { items: [string, boolean][] }) {
  return (
    <ul className="conds">
      {items.map(([t, ok], i) => (
        <li key={i} className={ok ? "ok" : "no"}>{ok ? "✓" : "✗"} {t}</li>
      ))}
    </ul>
  );
}
