"""Bollinger-band reversion — fade a band spike, enter when price re-enters.

Same mean-reversion idea as ``rsi_reversion``, measured differently: a close
*outside* the band that closes back *inside* is a stretch-and-spring. It and
the RSI strategy rarely fire on the same bar (one is momentum-based, the other
volatility-based), which is precisely the decorrelation an ensemble wants —
two votes from the same family is worth less than one.

Entry requires the previous bar outside and the current bar back inside, so we
never buy in the middle of a continuing run. Stop-loss is ATR-based.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..data import indicators as ta
from ..models import Action, Bar, Position, Signal
from .base import Strategy


class BollingerReversion(Strategy):
    name = "bollinger_reversion"

    def __init__(self, params: Optional[dict] = None):
        params = params or {}
        self.period = int(params.get("period", 20))
        self.num_std = float(params.get("num_std", 2.0))
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

        mid = ta.sma(close, self.period)
        sd = ta.rolling_std(close, self.period)
        a = ta.atr(high, low, close, self.atr_period)
        if any(np.isnan(x) for x in (mid[-1], mid[-2], sd[-1], sd[-2], a[-1])):
            return Signal(Action.HOLD, symbol, reason="indicator warm-up")

        lower_prev = float(mid[-2] - self.num_std * sd[-2])
        lower_now = float(mid[-1] - self.num_std * sd[-1])
        upper_prev = float(mid[-2] + self.num_std * sd[-2])
        upper_now = float(mid[-1] + self.num_std * sd[-1])

        c_prev, c_now = float(close[-2]), float(close[-1])
        entry = c_now
        atrv = float(a[-1])

        if c_prev < lower_prev and c_now >= lower_now:  # pierced the lower band, sprang back
            return Signal(Action.OPEN_LONG, symbol,
                          reason=f"band reversion: back above lower band ({c_prev:.5f}→{c_now:.5f})",
                          stop_loss=round(entry - self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "mid": float(mid[-1]), "sd": float(sd[-1])})

        if c_prev > upper_prev and c_now <= upper_now:  # pierced the upper band, snapped back
            if not self.allow_short:
                return Signal(Action.CLOSE, symbol, reason="upper-band turn (long-only, flatten)")
            return Signal(Action.OPEN_SHORT, symbol,
                          reason=f"band reversion: back below upper band ({c_prev:.5f}→{c_now:.5f})",
                          stop_loss=round(entry + self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "mid": float(mid[-1]), "sd": float(sd[-1])})

        return Signal(Action.HOLD, symbol, reason="inside the bands")
