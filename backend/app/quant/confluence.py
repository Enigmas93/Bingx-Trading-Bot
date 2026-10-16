"""Votação / confluência: agrupa sinais aceitos do mesmo par, direção e candle.

1 estratégia  → SINGLE_SIGNAL
2 estratégias → CONFLUENCE
3+            → STRONG_CONFLUENCE
A estratégia primária é a de maior score (desempate: maior R esperado); as demais são de apoio.
Os níveis (stop/alvo) usados na execução são os da primária.
"""

from dataclasses import dataclass, field

LEVELS = ["SINGLE_SIGNAL", "CONFLUENCE", "STRONG_CONFLUENCE"]
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}


def level_for(n: int) -> str:
    return LEVELS[min(max(n, 1), 3) - 1]


def group_id(symbol: str, direction: str, candle_time: int) -> str:
    return f"CG-{candle_time}-{symbol}-{direction}"


@dataclass
class ConfluenceGroup:
    symbol: str
    direction: str
    candle_time: int
    signals: list = field(default_factory=list)   # SignalCandidate

    @property
    def level(self) -> str:
        return level_for(len(self.signals))

    @property
    def primary(self):
        return max(self.signals, key=lambda s: (s.score, s.reward_r))

    @property
    def supporting(self) -> list:
        p = self.primary
        return [s for s in self.signals if s is not p]

    @property
    def group_id(self) -> str:
        return group_id(self.symbol, self.direction, self.candle_time)

    @property
    def strategies(self) -> list[str]:
        return sorted(s.strategy_id for s in self.signals)

    @property
    def combination(self) -> str:
        return "+".join(self.strategies)


def build_groups(symbol: str, candle_time: int, accepted: list) -> list[ConfluenceGroup]:
    groups: dict[str, ConfluenceGroup] = {}
    for s in accepted:
        g = groups.setdefault(s.direction, ConfluenceGroup(symbol, s.direction, candle_time))
        g.signals.append(s)
    return list(groups.values())
