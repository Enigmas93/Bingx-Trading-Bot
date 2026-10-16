"""Motor de saídas (avaliado a cada candle fechado do timeframe do trade, com dados da conta real).

STOP / TARGET            ordens na corretora (detectadas pelo monitor)
TIME_EXIT                trade aberto há mais candles que o máximo da estratégia primária
REGIME_CHANGE            regime do BTC virou contra a direção (LONG em BTC_BEAR_TREND, SHORT em BTC_BULL_TREND)
MOMENTUM_LOSS            estratégias de tendência: fechamento do lado errado da EMA21 com MACD virado (após 2 candles)
EXIT_SIGNAL              alguma estratégia emitiu sinal aceito na direção oposta no mesmo par
MANUAL_DEMO_EXIT         usuário fechou pelo painel
Cada regra pode ser ligada/desligada em QuantConfig; o motivo detalhado vai para o diário.
"""

import math
from datetime import timezone

HOSTILE = {"LONG": "BTC_BEAR_TREND", "SHORT": "BTC_BULL_TREND"}


def evaluate_exit(trade: dict, fv, btc_regime: str, opposite_signals: list[str], cfg, max_hold_bars: int,
                  exit_style: str, now_ms: int, bar_ms: int = 3_600_000) -> tuple[str, str] | None:
    """Retorna (motivo, detalhe) ou None. `fv` = FeatureView do par no último candle fechado (pode ser None)."""
    entry = trade["entry_time"]
    if entry.tzinfo is None:
        entry = entry.replace(tzinfo=timezone.utc)
    bars = (now_ms - entry.timestamp() * 1000) / bar_ms
    d = trade["direction"]

    if max_hold_bars and bars >= max_hold_bars:
        return "TIME_EXIT", f"{bars:.0f} candles abertos ≥ máximo de {max_hold_bars} da estratégia {trade['strategy_primary']}"
    if cfg.exit_on_regime_change and trade["symbol"] != "BTC-USDT" and btc_regime == HOSTILE[d] \
            and trade["btc_regime"] != btc_regime:
        return "REGIME_CHANGE", f"regime do BTC mudou de {trade['btc_regime']} para {btc_regime} (contra {d})"
    if cfg.exit_on_opposite_signal and opposite_signals:
        return "EXIT_SIGNAL", f"sinal oposto aceito de: {', '.join(sorted(opposite_signals))}"
    tfs = getattr(cfg, "momentum_exit_timeframes", None)
    momentum_on = cfg.exit_on_momentum_loss and (tfs is None or trade.get("timeframe", "1h") in tfs)
    if fv is not None and momentum_on and exit_style == "trend" and bars >= 2:
        c, e21, hist = fv["close"], fv["ema21"], fv["macd_hist"]
        if not any(math.isnan(x) for x in (c, e21, hist)):
            if (d == "LONG" and c < e21 and hist < 0) or (d == "SHORT" and c > e21 and hist > 0):
                return "MOMENTUM_LOSS", (f"fechamento {'abaixo' if d == 'LONG' else 'acima'} da EMA21 com histograma "
                                         f"MACD {'negativo' if d == 'LONG' else 'positivo'}")
    return None
