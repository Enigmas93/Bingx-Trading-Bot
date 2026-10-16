"""Registro de estratégias: instâncias, definições versionadas e configuração operacional.

Versionamento: a definição de cada (strategy_id, version) é gravada uma única vez na tabela `strategies` e nunca é
sobrescrita. Se o código mudar a definição sem trocar a versão, o registro acusa DEFINITION_CHANGED_WITHOUT_VERSION
(evento ERROR) e a estratégia fica bloqueada até a versão ser incrementada — assim resultados antigos nunca se
misturam com regras novas.
"""

import json

from sqlalchemy import select

from ..database import session_scope
from . import events
from .models import QuantConfig, StrategyDefinition, StrategySettings
from .strategies import CATALOG
from .strategies.base import Strategy

TIMEFRAMES = ["1h", "15m", "5m"]   # ordem de processamento no ciclo
TF_MS = {"1h": 3_600_000, "15m": 900_000, "5m": 300_000}
STRATEGIES: list[Strategy] = [cls(tf) for cls in CATALOG if cls.role == "signal" for tf in TIMEFRAMES] +     [cls() for cls in CATALOG if cls.role != "signal"]
BY_ID: dict[str, Strategy] = {s.strategy_id: s for s in STRATEGIES}
SIGNAL_STRATEGIES = [s for s in STRATEGIES if s.role == "signal"]
FILTERS = [s for s in STRATEGIES if s.role == "filter"]

_blocked: dict[str, str] = {}   # strategy_id -> motivo (definição alterada sem nova versão)


def _norm(d: dict) -> dict:
    return json.loads(json.dumps(d, sort_keys=True, default=list))


def sync_definitions() -> dict:
    """Grava definições novas, cria configurações padrão e detecta mudanças sem versão. Idempotente."""
    report = {"created": [], "conflicts": [], "invalid": []}
    with session_scope() as db:
        if db.get(QuantConfig, 1) is None:
            db.add(QuantConfig(id=1))
        for s in STRATEGIES:
            errs = s.validate()
            if errs:
                report["invalid"].append({s.strategy_id: errs})
                _blocked[s.strategy_id] = "definição inválida: " + "; ".join(errs)
                continue
            current = _norm(s.definition())
            row = db.scalar(select(StrategyDefinition).where(StrategyDefinition.strategy_id == s.strategy_id,
                                                             StrategyDefinition.version == s.version))
            if row is None:
                db.add(StrategyDefinition(strategy_id=s.strategy_id, version=s.version, name=s.name, family=s.family,
                                          role=s.role, definition=current))
                report["created"].append(f"{s.strategy_id} v{s.version}")
            elif _norm(row.definition) != current:
                msg = (f"{s.strategy_id} v{s.version}: a definição mudou no código sem nova versão. "
                       "Incremente a versão para registrar a nova hipótese (o histórico da versão antiga é preservado).")
                _blocked[s.strategy_id] = msg
                report["conflicts"].append(msg)
                events.emit("ERROR", strategy=s.strategy_id, message=msg, code="DEFINITION_CHANGED_WITHOUT_VERSION")
                continue
            _blocked.pop(s.strategy_id, None)

            st = db.get(StrategySettings, s.strategy_id)
            if st is None:
                db.add(StrategySettings(
                    strategy_id=s.strategy_id, active_version=s.version, enabled=True,
                    min_score=s.default_min_score, timeframe=s.timeframe,
                    allow_long="LONG" in s.supported_directions, allow_short="SHORT" in s.supported_directions,
                    accepted_regimes=list(s.default_regimes), cooldown_minutes=s.default_cooldown_minutes,
                    max_trades_per_day=s.default_max_trades_per_day, health="ACTIVE"))
            elif st.active_version != s.version:
                old = st.active_version
                st.active_version = s.version
                events.emit("STRATEGY_UPDATED", strategy=s.strategy_id,
                            message=f"nova versão ativa {old} → {s.version} (histórico de {old} preservado)")
    return report


def blocked_reason(strategy_id: str) -> str | None:
    return _blocked.get(strategy_id)


def settings_map() -> dict[str, dict]:
    with session_scope() as db:
        rows = db.scalars(select(StrategySettings)).all()
        return {r.strategy_id: {c.name: getattr(r, c.name) for c in StrategySettings.__table__.columns} for r in rows}


def quant_config() -> QuantConfig:
    with session_scope() as db:
        return db.get(QuantConfig, 1)


def definitions_history(strategy_id: str) -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(StrategyDefinition).where(StrategyDefinition.strategy_id == strategy_id)
                          .order_by(StrategyDefinition.created_at)).all()
        return [{"version": r.version, "created_at": r.created_at.isoformat(), "definition": r.definition} for r in rows]


EDITABLE_SETTINGS = {"enabled": bool, "min_score": float, "allow_long": bool, "allow_short": bool,
                     "accepted_regimes": list, "cooldown_minutes": int, "max_trades_per_day": int}


def update_settings(strategy_id: str, changes: dict) -> dict:
    """Altera a configuração operacional (não a definição/parâmetros, que exigem nova versão)."""
    from .regime import REGIMES

    if strategy_id not in BY_ID:
        raise KeyError(strategy_id)
    clean = {}
    for k, v in changes.items():
        if k not in EDITABLE_SETTINGS:
            raise ValueError(f"campo não editável: {k} (parâmetros da estratégia exigem nova versão no código)")
        if k == "accepted_regimes":
            bad = [r for r in v if r not in REGIMES]
            if bad:
                raise ValueError(f"regimes inválidos: {bad}")
            clean[k] = list(v)
        elif k == "min_score":
            if not 0 <= float(v) <= 100:
                raise ValueError("min_score deve ficar entre 0 e 100")
            clean[k] = float(v)
        else:
            clean[k] = EDITABLE_SETTINGS[k](v)
    with session_scope() as db:
        st = db.get(StrategySettings, strategy_id)
        before = {k: getattr(st, k) for k in clean}
        for k, v in clean.items():
            setattr(st, k, v)
        if "enabled" in clean and clean["enabled"] != before["enabled"]:
            st.health = "ACTIVE" if clean["enabled"] else "DISABLED"
            st.health_reason = None if clean["enabled"] else "desativada manualmente pelo usuário"
    if "enabled" in clean and clean["enabled"] != before["enabled"]:
        events.emit("STRATEGY_ENABLED" if clean["enabled"] else "STRATEGY_DISABLED", strategy=strategy_id,
                    message="alterado pelo usuário (histórico preservado)")
    other = {k: v for k, v in clean.items() if k != "enabled"}
    if other:
        events.emit("STRATEGY_UPDATED", strategy=strategy_id, message="configuração alterada",
                    before={k: before[k] for k in other}, after=other)
    return settings_map()[strategy_id]
