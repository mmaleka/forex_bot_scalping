"""Trailing take-profit — a stop that follows price as it runs in our favor.

The shared exit math, driven identically by the backtester (bar highs/lows)
and the live orchestrator (quotes → broker SL modifications):

  1. **dormant** until the position is ``activation`` in profit (from entry);
  2. **then** the stop rides ``distance`` behind the best price seen,
     ratcheting only *toward* the price — it never moves back;
  3. **re-placed** only when it improves by at least ``step`` (no spam of
     modification calls, and no jittering on noise).

This is the scalping exit for when there is no fixed target: winners get
locked in incrementally, losers keep the original ATR stop. The decision is
a pure function of (side, entry, current SL, best price) — no I/O — so the
backtest and the live path can never drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Side


@dataclass(frozen=True)
class TrailingStop:
    enabled: bool
    activation: float  # price units of profit before trailing starts
    distance: float    # price units the stop stays behind the best price
    step: float        # min stop improvement before re-placing

    @classmethod
    def from_settings(cls, s, point: float) -> "TrailingStop":
        """Build from settings, converting point-based config into price units
        using the symbol's point value (e.g. 1e-5 for a 5-digit pair)."""
        t = s.trailing
        return cls(
            enabled=bool(t.enabled),
            activation=float(t.activation_points) * point,
            distance=float(t.trail_points) * point,
            step=float(t.step_points) * point,
        )

    def new_stop(self, side: Side, entry: float, current_sl: Optional[float],
                 best_price: float) -> Optional[float]:
        """Return the new SL level if the trail should advance, else ``None``.

        ``best_price`` is the most favorable price seen since entry (highest
        bid for a long, lowest ask for a short) — not necessarily the current
        price. ``current_sl`` is the stop in place now (``None`` = unknown).
        """
        if not self.enabled or entry <= 0 or best_price <= 0:
            return None

        # Prices live on a 1e-5 grid; an epsilon far below that grid keeps
        # boundary cases (exactly-at-activation, exactly-at-step) robust
        # against float representation.
        eps = 1e-12

        if side is Side.BUY:
            if best_price - entry < self.activation - eps:
                return None  # not yet in profit enough to start trailing
            candidate = best_price - self.distance
            if current_sl is None or candidate - current_sl >= self.step - eps:
                return candidate
            return None
        else:  # Side.SELL
            if entry - best_price < self.activation - eps:
                return None
            candidate = best_price + self.distance
            if current_sl is None or current_sl - candidate >= self.step - eps:
                return candidate
            return None


__all__ = ["TrailingStop"]
