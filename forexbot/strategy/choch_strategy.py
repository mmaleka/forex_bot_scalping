"""CHoCH (change of character) — a structural reversal member of the ensemble.

It is an ordinary member, NOT a swing vote: it stays silent unless price
actually BREAKS a confirmed swing — the classic change-of-character event —
and votes only on that bar:

  * a swing high is a bar whose high beats the ``pivot_k`` bars on each
    side (so it is only "confirmed" ``pivot_k`` bars later);
  * bullish CHoCH: after lower-high structure (the latest swing high is
    below the one before it), the close crosses ABOVE that swing high on
    the newest bar -> OPEN_LONG;
  * bearish CHoCH: mirror — higher-low structure, close crosses BELOW the
    latest swing low -> OPEN_SHORT;
  * stop-loss is ATR-based like the other members, so it contributes to
    the ensemble's weighted-median stop.

This is a *reversal* member (structure flip), complementing the trend
side (ma_cross, donchian_breakout) rather than duplicating a breakout.

Why the rewrite: the previous version voted every bar from the close's
position inside the bar range, ((close-low)/(high-low)). On this feed
~93% of minute bars close exactly at one of the range extremes
(one-sided, sparse-quote microstructure), so that value is a coin flip
and the member was voting ~98% of the time — effectively the
ensemble's second vote instead of a member.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..data import indicators as ta
from ..models import Action, Bar, Position, Signal
from .base import Strategy


def _is_swing_high(high: np.ndarray, i: int, k: int) -> bool:
    """Fractal high: strictly above the ``k`` bars on each side."""
    for d in range(1, k + 1):
        if not (high[i] > high[i - d] and high[i] > high[i + d]):
            return False
    return True


def _is_swing_low(low: np.ndarray, i: int, k: int) -> bool:
    for d in range(1, k + 1):
        if not (low[i] < low[i - d] and low[i] < low[i + d]):
            return False
    return True


class ChochStrategy(Strategy):
    name = "choch_strategy"

    def __init__(self, params: Optional[dict] = None):
        self.params = params or {}
        self.pivot_k = int(self.params.get("pivot_k", 2))
        self.atr_mult = float(self.params.get("atr_mult", 2.0))
        self.atr_period = int(self.params.get("atr_period", 14))
        self.allow_short = bool(self.params.get("allow_short", True))

    def on_bar(self, bars: List[Bar], position: Optional[Position]) -> Signal:
        k = self.pivot_k
        need = 2 * k + 3  # room for one full swing + the ATR warm-up
        if len(bars) < need:
            return Signal(Action.HOLD, bars[-1].symbol if bars else "",
                          reason="warming up")

        high = np.array([b.high for b in bars], dtype=float)
        low = np.array([b.low for b in bars], dtype=float)
        close = np.array([b.close for b in bars], dtype=float)
        symbol = bars[-1].symbol
        n = len(bars)

        a = ta.atr(high, low, close, self.atr_period)
        if np.isnan(a[-1]):
            return Signal(Action.HOLD, symbol, reason="indicator warm-up")
        atrv = float(a[-1])
        entry = float(close[-1])

        # Confirmed swings only (a pivot needs k bars AFTER it to be known).
        sh_idx = [i for i in range(k, n - k) if _is_swing_high(high, i, k)]
        sl_idx = [i for i in range(k, n - k) if _is_swing_low(low, i, k)]

        # Bullish CHoCH: lower-high structure, close crosses above the
        # latest swing high ON THE NEWEST BAR (close[-2] was at/below it).
        bullish = False
        sh_val = None
        if sh_idx:
            i = sh_idx[-1]
            sh_val = float(high[i])
            lower_high = len(sh_idx) < 2 or high[i] < high[sh_idx[-2]]
            if lower_high and close[-2] <= sh_val < close[-1]:
                bullish = True

        # Bearish CHoCH: higher-low structure, close crosses below the
        # latest swing low on the newest bar.
        bearish = False
        sl_val = None
        if sl_idx:
            i = sl_idx[-1]
            sl_val = float(low[i])
            higher_low = len(sl_idx) < 2 or low[i] > low[sl_idx[-2]]
            if higher_low and close[-2] >= sl_val > close[-1]:
                bearish = True

        if bullish and not bearish:
            return Signal(Action.OPEN_LONG, symbol,
                          reason=f"CHoCH: close broke the last lower high ({sh_val:.5f})",
                          stop_loss=round(entry - self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "choch": "bull", "swing": sh_val})
        if bearish and not bullish:
            if not self.allow_short:
                return Signal(Action.HOLD, symbol, reason="CHoCH bearish (long-only, flatten)")
            return Signal(Action.OPEN_SHORT, symbol,
                          reason=f"CHoCH: close broke the last higher low ({sl_val:.5f})",
                          stop_loss=round(entry + self.atr_mult * atrv, 5),
                          meta={"atr": atrv, "choch": "bear", "swing": sl_val})
        return Signal(Action.HOLD, symbol, reason="no structure break")
