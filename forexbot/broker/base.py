"""The ``Broker`` contract — the ONLY place vendor-specific code may live.

Everything above this interface (strategy, risk, engine, backtest) speaks these
normalized types and never touches a vendor object directly. Swapping Tickerall
for another execution provider means writing one new adapter, not rewriting the bot.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import (
    AccountState,
    Bar,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
    SymbolSpec,
)


class Broker(ABC):
    """Normalized trading interface shared by live and backtest paths."""

    #: Populated by ``connect()``.
    account_id: Optional[str] = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    @abstractmethod
    def connect(self) -> AccountState:
        """Establish the session and return the initial account state."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear the session down cleanly (best-effort)."""

    # ── market data ────────────────────────────────────────────────────────
    @abstractmethod
    def quote(self, symbol: str) -> Quote:
        """Best current bid/ask for ``symbol``."""

    @abstractmethod
    def candles(self, symbol: str, timeframe: str, count: int) -> List[Bar]:
        """The most recent ``count`` closed bars, oldest → newest."""

    # ── trading ────────────────────────────────────────────────────────────
    @abstractmethod
    def place(self, order: OrderRequest) -> OrderResult:
        """Submit a sized order. Must be safe to retry (idempotent)."""

    @abstractmethod
    def close(self, ticket: str, volume: Optional[float] = None) -> OrderResult:
        """Close a position (fully if ``volume`` is None)."""

    def modify_position(self, ticket: str, stop_loss: Optional[float] = None,
                        take_profit: Optional[float] = None) -> OrderResult:
        """Move an open position's SL/TP (trailing take-profit, breakeven).

        Not abstract: a broker without the endpoint simply reports not-
        supported, and callers (the trailing engine) degrade gracefully by
        keeping the stop where it is.
        """
        return OrderResult(ok=False, ticket=ticket,
                           raw="modify_position not supported by this broker")

    # ── state ──────────────────────────────────────────────────────────────
    @abstractmethod
    def positions(self) -> List[Position]:
        """Currently open positions."""

    @abstractmethod
    def account(self) -> AccountState:
        """Account snapshot: balance, equity, margin, positions."""

    @abstractmethod
    def symbol_specs(self, symbol: str) -> SymbolSpec:
        """Lot/precision contract for ``symbol`` (needed to size lots)."""
