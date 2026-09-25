"""Broker-agnostic domain types shared across strategy, risk, engine, backtest.

These are plain value objects. A ``Broker`` adapter maps its native responses
to/from these types, so nothing above the broker layer ever touches a
vendor-specific object. Keeping them dependency-free means the backtester and
the live engine operate on exactly the same data shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class Action(str, Enum):
    """A strategy's intent, before the risk manager vets and sizes it."""
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE = "close"
    HOLD = "hold"


@dataclass
class Bar:
    """A single OHLC bar. ``timestamp`` is the bar *open* time, UTC."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    timestamp: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Signal:
    """What a strategy wants to do. The risk manager may veto or resize it."""
    action: Action
    symbol: str
    reason: str = ""
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    meta: Dict = field(default_factory=dict)


@dataclass
class OrderRequest:
    """A fully-specified, sized order, ready to hand to the broker."""
    symbol: str
    side: Side
    volume: float  # broker base units (lots)
    order_type: OrderType = OrderType.MARKET
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    price: Optional[float] = None
    idempotency_key: str = ""


@dataclass
class Position:
    ticket: str
    symbol: str
    side: Side
    volume: float
    open_price: float
    profit: float = 0.0
    opened_at: Optional[datetime] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class AccountState:
    account_id: str
    currency: str
    balance: float
    equity: float
    margin_free: float = 0.0
    positions: List[Position] = field(default_factory=list)

    @property
    def open_position_count(self) -> int:
        return len(self.positions)


@dataclass
class OrderResult:
    """Normalized result of a place/close call (vendor-neutral)."""
    ok: bool
    ticket: Optional[str] = None
    status: Optional[str] = None
    raw: Optional[object] = None


@dataclass
class SymbolSpec:
    """Contract / precision info needed to size lots and price SL/TP."""
    symbol: str
    base_currency: str = ""
    quote_currency: str = ""
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    digits: int = 5
    point: float = 1e-5
    margin_currency: str = ""
    spread_typical: float = 0.0


@dataclass
class RiskDecision:
    """The risk manager's verdict on a signal."""
    approved: bool
    order: Optional["OrderRequest"] = None
    reason: str = ""
