"""The ``Strategy`` contract.

A strategy is a pure decision function: given the closed-bar window and the
current position, return an intent. It does no I/O, no sizing, no order logic —
that's the risk manager's and engine's job. That separation is what lets the
same strategy run unchanged in backtest, paper, and live.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import Bar, Position, Signal


class Strategy(ABC):
    name: str = "base"

    def __init__(self, params: Optional[dict] = None):
        self.params = params or {}

    @abstractmethod
    def on_bar(self, bars: List[Bar], position: Optional[Position]) -> Signal:
        """Inspect the closed-bar window and current position; return an intent.

        ``bars`` is oldest → newest and already excludes the still-forming bar.
        ``position`` is the bot's current position in the traded symbol, if any.
        """
