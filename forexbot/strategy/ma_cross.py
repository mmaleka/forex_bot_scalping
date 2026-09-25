"""EMA fast/slow crossover — the first strategy, chosen to prove the pipeline.

It is deliberately not the money-maker: it's transparent, easy to backtest, and
exercises the full open → risk → fill → SL → close loop. A real scalping strategy
slots in later via the same ``Strategy`` interface without touching the rest.

Entry is a *cross event* (fast EMA crossing slow), so the bot stays flat until a
cross and reverses on the opposite cross. Stop-loss is ATR-based.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..data import indicators as ta
from ..models import Action, Bar, Position, Signal
from .base import Strategy


class MaCross(Strategy):
    name = "ma_cross"

    def __init__(self, params: Optional[dict] = None):
        params = params or {}
        self.fast = int(params.get("fast", 20))
        self.slow = int(params.get("slow", 50))
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.atr_period = int(params.get("atr_period", 14))
        self.allow_short = bool(params.get("allow_short", True))

    def on_bar(self, bars: List[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < self.slow + 2:
            return Signal(Action.HOLD, bars[-1].symbol if bars else "", reason="warming up")

        close = np.array([b.close for b in bars], dtype=float)
        high = np.array([b.high for b in bars], dtype=float)
        low = np.array([b.low for b in bars], dtype=float)
        symbol = bars[-1].symbol

        f = ta.ema(close, self.fast)
        s = ta.ema(close, self.slow)
        a = ta.atr(high, low, close, self.atr_period)
        if any(np.isnan(x) for x in (f[-1], f[-2], s[-1], s[-2], a[-1])):
            return Signal(Action.HOLD, symbol, reason="indicator warm-up")

        prev_above = f[-2] > s[-2]
        cur_above = f[-1] > s[-1]
        entry = float(close[-1])
        atrv = float(a[-1])

        if not prev_above and cur_above:  # golden cross
            return Signal(Action.OPEN_LONG, symbol,
                          reason=f"golden cross EMA{self.fast}/{self.slow}",
                          stop_loss=round(entry - self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "fast": float(f[-1]), "slow": float(s[-1])})

        if prev_above and not cur_above:  # death cross
            if not self.allow_short:
                return Signal(Action.CLOSE, symbol, reason="death cross (long-only, flatten)")
            return Signal(Action.OPEN_SHORT, symbol,
                          reason=f"death cross EMA{self.fast}/{self.slow}",
                          stop_loss=round(entry + self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "fast": float(f[-1]), "slow": float(s[-1])})

        return Signal(Action.HOLD, symbol, reason="no cross")
