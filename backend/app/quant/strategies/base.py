"""Interface comum de todas as estratégias.

Cada estratégia é uma HIPÓTESE quantitativa independente:
- avalia LONG e SHORT em funções separadas (evaluate_long / evaluate_short) com parâmetros próprios por direção;
- devolve um SignalCandidate com score 0-100, confiança, motivo legível, condições verificadas, preço de referência,
  stop lógico e alvo lógico;
- declara regimes do BTC aceitos por padrão (configuráveis e validados pelos resultados, não assumidos).
A definição (parâmetros + regras) é versionada: mudar parâmetros exige nova versão (ver registry.py).
"""

import math
from dataclasses import dataclass, field

from ..features import FeatureView

TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


@dataclass
class SignalCandidate:
    strategy_id: str
    version: str
    direction: str
    score: float
    confidence: float
    reason: str
    conditions: list            # [(texto, ok_bool)]
    ref_price: float
    stop: float
    target: float
    timeframe: str
    features_used: dict = field(default_factory=dict)
    max_hold_bars: int = 48
    exit_style: str = "trend"

    @property
    def risk(self) -> float:
        return abs(self.ref_price - self.stop)

    @property
    def reward_r(self) -> float:
        return abs(self.target - self.ref_price) / self.risk if self.risk > 0 else 0.0


class Strategy:
    strategy_id: str = "BASE"
    name: str = "Base"
    version: str = "1.0"
    family: str = "base"
    role: str = "signal"             # signal | filter
    timeframe: str = "1h"
    supported_directions: tuple = ("LONG", "SHORT")
    default_regimes: tuple = ()
    default_min_score: float = 60
    default_cooldown_minutes: int = 120
    default_max_trades_per_day: int = 6
    max_hold_bars: int = 48
    exit_style: str = "trend"        # trend (sai por perda de momentum) | reversion (sai por alvo/tempo) | none
    description: str = ""
    feature_keys: tuple = ()
    long_params: dict = {}
    short_params: dict = {}

    def __init__(self, timeframe: str | None = None):
        """Cada estratégia pode rodar em vários timeframes. A variante 1h mantém o id original (histórico preservado);
        as demais ganham sufixo (ex.: BREAKOUT_15M), com configuração, desempenho e saúde próprios."""
        self.base_id = type(self).strategy_id
        if timeframe:
            self.timeframe = timeframe
        if self.role == "signal" and self.timeframe != "1h":
            self.strategy_id = f"{self.base_id}_{self.timeframe.upper()}"
            # cooldown proporcional ao timeframe (mín. 15 min)
            self.default_cooldown_minutes = max(15, int(type(self).default_cooldown_minutes * TF_MINUTES[self.timeframe] / 60))

    # ------------------------------------------------------------ interface
    def calculate_features(self, fv: FeatureView) -> dict:
        snap = {}
        for k in self.feature_keys:
            x = fv[k]
            snap[k] = None if math.isnan(x) else round(x, 8)
        return snap

    def evaluate_long(self, fv: FeatureView, ctx: dict) -> SignalCandidate | None:  # pragma: no cover
        return None

    def evaluate_short(self, fv: FeatureView, ctx: dict) -> SignalCandidate | None:  # pragma: no cover
        return None

    def generate_signal(self, fv: FeatureView, ctx: dict, directions=("LONG", "SHORT")) -> list[SignalCandidate]:
        out = []
        if not fv.ok("close", "atr"):
            return out
        if "LONG" in directions and "LONG" in self.supported_directions:
            s = self.evaluate_long(fv, ctx)
            if s:
                out.append(s)
        if "SHORT" in directions and "SHORT" in self.supported_directions:
            s = self.evaluate_short(fv, ctx)
            if s:
                out.append(s)
        return out

    def explain_signal(self, sig: SignalCandidate) -> str:
        lines = [f"{self.name} ({self.strategy_id} v{self.version}) — {sig.direction} score {sig.score:.0f}"]
        lines += [f"{'✓' if ok else '✗'} {txt}" for txt, ok in sig.conditions]
        lines.append(f"Referência {sig.ref_price:.6g} · stop {sig.stop:.6g} · alvo {sig.target:.6g} ({sig.reward_r:.2f}R)")
        lines.append(f"Motivo: {sig.reason}")
        return "\n".join(lines)

    def validate(self) -> list[str]:
        errors = []
        if not self.strategy_id or not self.version:
            errors.append("strategy_id/version obrigatórios")
        for d in self.supported_directions:
            if d not in ("LONG", "SHORT"):
                errors.append(f"direção inválida: {d}")
        if self.timeframe not in TF_MINUTES:
            errors.append(f"timeframe não suportado: {self.timeframe}")
        if not (0 <= self.default_min_score <= 100):
            errors.append("default_min_score fora de 0-100")
        for side, p in (("long", self.long_params), ("short", self.short_params)):
            for k, v in p.items():
                if not isinstance(v, (int, float, bool)):
                    errors.append(f"parâmetro {side}.{k} não numérico")
        return errors

    def get_metadata(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "base_id": self.base_id, "name": self.name, "version": self.version, "family": self.family,
            "role": self.role, "timeframe": self.timeframe, "supported_directions": list(self.supported_directions),
            "default_regimes": list(self.default_regimes), "default_min_score": self.default_min_score,
            "default_cooldown_minutes": self.default_cooldown_minutes,
            "default_max_trades_per_day": self.default_max_trades_per_day, "max_hold_bars": self.max_hold_bars,
            "exit_style": self.exit_style, "description": self.description,
            "long_params": dict(self.long_params), "short_params": dict(self.short_params),
        }

    def definition(self) -> dict:
        """O que define a versão. Qualquer mudança aqui exige nova versão."""
        m = self.get_metadata()
        return {k: m[k] for k in ("name", "family", "role", "timeframe", "supported_directions", "default_regimes",
                                  "max_hold_bars", "exit_style", "long_params", "short_params", "description")}

    # ------------------------------------------------------------ helpers
    def _make(self, fv: FeatureView, direction: str, score: float, conditions: list, stop: float, target: float,
              reason: str, min_stop_atr: float = 0.5, max_stop_atr: float = 4.0, min_reward_r: float = 1.0):
        """Monta o sinal validando os níveis. Retorna None se o sinal não fizer sentido operacional."""
        price, atr = fv["close"], fv["atr"]
        if any(math.isnan(x) for x in (price, atr, stop, target)) or atr <= 0:
            return None
        if direction == "LONG" and not (stop < price < target):
            return None
        if direction == "SHORT" and not (target < price < stop):
            return None
        risk = abs(price - stop)
        if risk < min_stop_atr * atr or risk > max_stop_atr * atr:
            return None
        if abs(target - price) / risk < min_reward_r - 1e-9:
            return None
        score = max(0.0, min(100.0, score))
        ok_frac = sum(1 for _, ok in conditions if ok) / max(1, len(conditions))
        return SignalCandidate(
            strategy_id=self.strategy_id, version=self.version, direction=direction, score=round(score, 1),
            confidence=round(score / 100 * ok_frac, 3), reason=reason, conditions=conditions, ref_price=price,
            stop=stop, target=target, timeframe=self.timeframe, features_used=self.calculate_features(fv),
            max_hold_bars=self.max_hold_bars, exit_style=self.exit_style)


def clamp01(x: float) -> float:
    if x is None or math.isnan(x):
        return 0.0
    return max(0.0, min(1.0, x))
