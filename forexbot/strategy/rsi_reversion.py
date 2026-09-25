"""RSI mean reversion — fade an extreme, enter on the turn back.

The counterweight to the ensemble's trend followers: while ``ma_cross`` and
``donchian_breakout`` chase momentum, this one fades an RSI flush and enters
the moment RSI turns back *through* the extreme level. In the choppy ranges
where FX actually lives, that turn is where the edge tends to be; in a strong
trend it gets stopped out — which is exactly why it belongs in an ensemble
rather than standalone.

Entry is a *level-cross event* (RSI crossing back over oversold/under
overbought), so the bot trades only the turn, never the middle of the range.
Stop-loss is ATR-based.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..data import indicators as ta
from ..models import Action, Bar, Position, Signal
from .base import Strategy


class RsiReversion(Strategy):
    name = "rsi_reversion"

    def __init__(self, params: Optional[dict] = None):
        params = params or {}
        self.period = int(params.get("period", 14))
        self.oversold = float(params.get("oversold", 30.0))
        self.overbought = float(params.get("overbought", 70.0))
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.atr_period = int(params.get("atr_period", 14))
        self.allow_short = bool(params.get("allow_short", True))

    def on_bar(self, bars: List[Bar], position: Optional[Position]) -> Signal:
        need = max(self.period, self.atr_period) + 2
        if len(bars) < need:
            return Signal(Action.HOLD, bars[-1].symbol if bars else "", reason="warming up")

        close = np.array([b.close for b in bars], dtype=float)
        high = np.array([b.high for b in bars], dtype=float)
        low = np.array([b.low for b in bars], dtype=float)
        symbol = bars[-1].symbol

        r = ta.rsi(close, self.period)
        a = ta.atr(high, low, close, self.atr_period)
        if any(np.isnan(x) for x in (r[-1], r[-2], a[-1])):
            return Signal(Action.HOLD, symbol, reason="indicator warm-up")

        r_prev, r_now = float(r[-2]), float(r[-1])
        entry = float(close[-1])
        atrv = float(a[-1])

        if r_prev < self.oversold <= r_now:  # flushed oversold, now turning up
            return Signal(Action.OPEN_LONG, symbol,
                          reason=f"RSI{self.period} turned up from oversold ({r_prev:.1f}→{r_now:.1f})",
                          stop_loss=round(entry - self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "rsi_prev": r_prev, "rsi": r_now})

        if r_prev > self.overbought >= r_now:  # spiked overbought, now turning down
            if not self.allow_short:
                return Signal(Action.CLOSE, symbol, reason="overbought turn (long-only, flatten)")
            return Signal(Action.OPEN_SHORT, symbol,
                          reason=f"RSI{self.period} turned down from overbought ({r_prev:.1f}→{r_now:.1f})",
                          stop_loss=round(entry + self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "rsi_prev": r_prev, "rsi": r_now})

        return Signal(Action.HOLD, symbol, reason=f"RSI {r_now:.1f} mid-range")
