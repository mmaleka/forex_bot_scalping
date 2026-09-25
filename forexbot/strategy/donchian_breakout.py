"""Donchian channel breakout — enter when price breaks the prior N-bar extreme.

The pure momentum leg of the ensemble: a close beyond the highest high (or
lowest low) of the *previous* ``lookback`` bars means sellers/buyers just
exhausted a full N-bar range in one bar. Turtle-style logic, deliberately
unsmoothed — no indicator lag, just the channel.

It is the natural opposite of the two reversion strategies: they buy the
stretch, this one buys the break. When the market trends the breakouts pay and
the fades get stopped; when it ranges, the reverse — so all three should be
in the vote, with the ensemble deciding which regime is live. Stop-loss is
ATR-based.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..data import indicators as ta
from ..models import Action, Bar, Position, Signal
from .base import Strategy


class DonchianBreakout(Strategy):
    name = "donchian_breakout"

    def __init__(self, params: Optional[dict] = None):
        params = params or {}
        self.lookback = int(params.get("lookback", 20))
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.atr_period = int(params.get("atr_period", 14))
        self.allow_short = bool(params.get("allow_short", True))

    def on_bar(self, bars: List[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < self.lookback + 2:
            return Signal(Action.HOLD, bars[-1].symbol if bars else "", reason="warming up")

        high = np.array([b.high for b in bars], dtype=float)
        low = np.array([b.low for b in bars], dtype=float)
        close = np.array([b.close for b in bars], dtype=float)
        symbol = bars[-1].symbol

        # Prior N-bar extremes, *excluding* the current bar — breaking them
        # today is what makes this a breakout, not a retest.
        hh = float(high[-1 - self.lookback: -1].max())
        ll = float(low[-1 - self.lookback: -1].min())
        a = ta.atr(high, low, close, self.atr_period)
        if np.isnan(a[-1]):
            return Signal(Action.HOLD, symbol, reason="indicator warm-up")

        entry = float(close[-1])
        atrv = float(a[-1])

        if entry > hh:
            return Signal(Action.OPEN_LONG, symbol,
                          reason=f"breakout above {self.lookback}-bar high ({hh:.5f} → {entry:.5f})",
                          stop_loss=round(entry - self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "channel_high": hh, "channel_low": ll})

        if entry < ll:
            if not self.allow_short:
                return Signal(Action.CLOSE, symbol, reason="downside breakout (long-only, flatten)")
            return Signal(Action.OPEN_SHORT, symbol,
                          reason=f"breakdown below {self.lookback}-bar low ({ll:.5f} → {entry:.5f})",
                          stop_loss=round(entry + self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "channel_high": hh, "channel_low": ll})

        return Signal(Action.HOLD, symbol, reason=f"inside {self.lookback}-bar channel")
