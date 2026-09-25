"""Ensemble — weighted vote across member strategies.

Every bar, each member strategy votes exactly as if it were running
standalone: OPEN_LONG, OPEN_SHORT, CLOSE, or HOLD. The ensemble aggregates
the votes by weight and trades only when agreement clears ``min_votes``
(weight units) *and* beats the opposing side:

  - flat  → open long/short once the favored side's weight ≥ min_votes and
            exceeds the other side's (2 of 4 equal-weighted members by default)
  - open  → CLOSE once enough members agree to exit, or once the *opposite*
            direction clears the bar (the engine then reverses position)
  - otherwise → HOLD, with the full tally in ``reason``

This is what a voting ensemble is for: solo false signals — a single
cross in the middle of a range, one lonely breakout — die in the vote, while
a genuine setup that two or three independent indicator families agree on
gets taken. Members stay completely independent: none sees the others'
votes, so there is no feedback loop.

The ensemble is itself a plain ``Strategy``, so the backtester and the live
orchestrator treat it exactly like any other strategy — same interface, same
risk gate, same position handling.

Config shape::

    strategy:
      name: ensemble
      params:
        min_votes: 2          # weight units required to open (default 2)
        min_close_votes: 2    # weight units required to close (default = min_votes)
        members:
          - name: ma_cross
            params: {fast: 20, slow: 50}
            weight: 1.0
          - name: donchian_breakout
            weight: 1.5       # e.g. trust breakouts a bit more
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from ..models import Action, Bar, Position, Side, Signal
from .base import Strategy


def _weighted_median(pairs: List[Tuple[float, float]]) -> float:
    """Weighted median of (value, weight) pairs — the stop that best represents
    the members who actually voted for this side."""
    vals = np.array([v for v, _ in pairs], dtype=float)
    ws = np.array([w for _, w in pairs], dtype=float)
    order = np.argsort(vals)
    vals, ws = vals[order], ws[order]
    cum = np.cumsum(ws)
    idx = int(np.searchsorted(cum, 0.5 * cum[-1]))
    return float(vals[min(idx, len(vals) - 1)])


class Ensemble(Strategy):
    name = "ensemble"

    def __init__(self, params: Optional[dict] = None):
        params = params or {}
        # Deferred import: ``make_strategy`` lives in the package __init__,
        # which imports this module — a top-level import would be circular.
        from . import make_strategy

        spec = params.get("members")
        if not spec:
            raise ValueError("ensemble requires a non-empty `members` list")
        self.members: List[Dict] = []
        for m in spec:
            self.members.append({
                "strategy": make_strategy(m["name"], m.get("params")),
                "weight": float(m.get("weight", 1.0)),
            })
        self.min_votes = float(params.get("min_votes", 2.0))
        self.min_close_votes = float(params.get("min_close_votes", self.min_votes))
        self.total_weight = sum(m["weight"] for m in self.members)

    def on_bar(self, bars: List[Bar], position: Optional[Position]) -> Signal:
        if len(self.members) < 2:
            raise ValueError("an ensemble needs at least two members")
        symbol = bars[-1].symbol if bars else ""
        if len(bars) < 2:
            return Signal(Action.HOLD, symbol, reason="warming up")

        long_w = short_w = close_w = 0.0
        long_sl: List[Tuple[float, float]] = []
        short_sl: List[Tuple[float, float]] = []
        atrs: List[float] = []
        long_who: List[str] = []
        short_who: List[str] = []
        close_who: List[str] = []

        for m in self.members:
            sig = m["strategy"].on_bar(bars, position)
            w = m["weight"]
            who = m["strategy"].name
            if sig.action == Action.OPEN_LONG:
                long_w += w
                long_who.append(who)
                if sig.stop_loss is not None:
                    long_sl.append((sig.stop_loss, w))
            elif sig.action == Action.OPEN_SHORT:
                short_w += w
                short_who.append(who)
                if sig.stop_loss is not None:
                    short_sl.append((sig.stop_loss, w))
            elif sig.action == Action.CLOSE:
                close_w += w
                close_who.append(who)
            if sig.meta.get("atr") is not None:
                atrs.append(float(sig.meta["atr"]))

        votes = {"long": round(long_w, 3), "short": round(short_w, 3),
                 "close": round(close_w, 3)}
        tally = f"long {long_w:.1f} / short {short_w:.1f} / close {close_w:.1f} (need {self.min_votes:.1f})"

        def _open(action: Action, side: str, who: List[str], sl_pairs, reason_extra: str) -> Signal:
            sl = round(_weighted_median(sl_pairs), 5) if sl_pairs else None
            return Signal(action, symbol,
                          reason=f"ensemble {side} {votes[side]:.1f}/{self.total_weight:.1f} "
                                 f"({', '.join(who)}) {reason_extra}",
                          stop_loss=sl,
                          meta={"atr": float(np.median(atrs)) if atrs else None,
                                "votes": votes, "members": [m["strategy"].name for m in self.members]})

        if position is None:
            if long_w >= self.min_votes and long_w > short_w:
                return _open(Action.OPEN_LONG, "long", long_who, long_sl, "")
            if short_w >= self.min_votes and short_w > long_w:
                return _open(Action.OPEN_SHORT, "short", short_who, short_sl, "")
            return Signal(Action.HOLD, symbol, reason=f"no consensus: {tally}", meta={"votes": votes})

        # Position is open.
        if close_w >= self.min_close_votes:
            return Signal(Action.CLOSE, symbol,
                          reason=f"ensemble close ({', '.join(close_who)}; {tally})",
                          meta={"votes": votes})
        # A strong *opposite* vote flips the position — the engine closes the
        # opposite side first, exactly as it does for any reversing strategy.
        if position.side is Side.SELL and long_w >= self.min_votes and long_w > short_w:
            return _open(Action.OPEN_LONG, "long", long_who, long_sl, "(reversal)")
        if position.side is Side.BUY and short_w >= self.min_votes and short_w > long_w:
            return _open(Action.OPEN_SHORT, "short", short_who, short_sl, "(reversal)")

        return Signal(Action.HOLD, symbol, reason=f"holding: {tally}", meta={"votes": votes})
