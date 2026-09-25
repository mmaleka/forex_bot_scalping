"""Offline portfolio simulator — simulated fills, P&L, and equity with a cost model.

The backtester uses this so the *exact same* strategy + risk code runs on
history as it does live. No network, no broker; the runner feeds it bar prices
via ``set_market()`` and drives fills. Costs (spread via bid/ask, slippage,
commission) are applied so a strategy that looks good here has already paid for
the things that kill most retail FX strategies.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..config import Settings
from ..models import (
    AccountState,
    Bar,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
    Side,
    SymbolSpec,
)
from ..utils import parse_pair
from .base import Broker

log = logging.getLogger("forexbot.broker.paper")

_TICKET = 0


def _next_ticket() -> str:
    global _TICKET
    _TICKET += 1
    return f"PAPER-{_TICKET:06d}"


class PaperBroker(Broker):
    def __init__(self, settings: Settings, start_equity: float = 10000.0,
                 commission_per_lot: float = 7.0, slippage_points: int = 1):
        self._s = settings
        self._start_equity = start_equity
        self._balance = start_equity
        self._commission_per_lot = commission_per_lot
        self._slippage_points = slippage_points
        self._positions = {}
        self._last = {}  # symbol -> (bid, ask)
        self._closed_trades: List[tuple] = []
        # currency code -> account-currency value of 1 unit (e.g. {"JPY": 1/152.5}).
        # Set via set_fx_rates(); parity is only the last-resort fallback.
        self._fx: Dict[str, float] = {}
        self.account_id = "paper"

    # ── lifecycle ──────────────────────────────────────────────────────────
    def connect(self) -> AccountState:
        self._balance = self._start_equity
        self._positions = {}
        self._last = {}
        return self.account()

    def disconnect(self) -> None:
        pass

    # ── market data (runner-driven) ────────────────────────────────────────
    def set_market(self, symbol: str, bid: float, ask: float) -> None:
        self._last[symbol] = (bid, ask)

    def quote(self, symbol: str) -> Quote:
        bid, ask = self._last.get(symbol, (0.0, 0.0))
        return Quote(symbol=symbol, bid=bid, ask=ask, timestamp=datetime.now(timezone.utc))

    def candles(self, symbol, timeframe, count) -> List[Bar]:
        raise NotImplementedError("Backtest: the runner feeds bars; PaperBroker does not fetch history.")

    # ── trading ────────────────────────────────────────────────────────────
    def place(self, order: OrderRequest) -> OrderResult:
        if order.symbol not in self._last:
            return OrderResult(ok=False, raw="no market price — call set_market() first")
        bid, ask = self._last[order.symbol]
        slip = self._slippage_points * 1e-5
        fill = ask + slip if order.side is Side.BUY else bid - slip
        cost = self._commission_per_lot * order.volume
        self._balance -= cost
        ticket = _next_ticket()
        self._positions[ticket] = Position(
            ticket=ticket, symbol=order.symbol, side=order.side,
            volume=order.volume, open_price=fill, profit=0.0,
            opened_at=datetime.now(timezone.utc),
        )
        log.info("paper fill %s %s %.2f @ %.5f (commission %.2f)",
                 order.side.value, order.symbol, order.volume, fill, cost)
        return OrderResult(ok=True, ticket=ticket, status="filled")

    def close(self, ticket: str, volume: Optional[float] = None) -> OrderResult:
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult(ok=False, raw=f"unknown ticket {ticket}")
        bid, ask = self._last.get(pos.symbol, (pos.open_price, pos.open_price))
        slip = self._slippage_points * 1e-5
        fill = (bid - slip) if pos.side is Side.BUY else (ask + slip)
        vol = volume or pos.volume
        contract = self._s.risk.contract_size
        direction = 1.0 if pos.side is Side.BUY else -1.0
        pnl = self._account_pnl((fill - pos.open_price) * vol * contract * direction,
                                pos.symbol, pos.open_price)
        cost = self._commission_per_lot * vol
        self._balance += pnl - cost
        pos.profit = pnl - cost
        self._closed_trades.append((ticket, pos.symbol, vol, fill, pnl - cost))
        if vol >= pos.volume:
            del self._positions[ticket]
        else:
            pos.volume -= vol
        log.info("paper close %s pnl=%.2f", ticket, pnl - cost)
        return OrderResult(ok=True, ticket=ticket, status="closed")

    def modify_position(self, ticket: str, stop_loss: Optional[float] = None,
                        take_profit: Optional[float] = None) -> OrderResult:
        # The PaperBroker is a fill/PnL simulator: SL/TP *levels* are owned by
        # whoever drives it (the Backtester in backtest, the engine in paper
        # runs against a real broker), so a modification is accepted as a no-op.
        if ticket not in self._positions:
            return OrderResult(ok=False, ticket=ticket, raw=f"unknown ticket {ticket}")
        return OrderResult(ok=True, ticket=ticket, status="modified")

    # ── state ──────────────────────────────────────────────────────────────
    def _mark_equity(self) -> float:
        equity = self._balance
        contract = self._s.risk.contract_size
        for p in self._positions.values():
            bid, ask = self._last.get(p.symbol, (p.open_price, p.open_price))
            cur = bid if p.side is Side.BUY else ask
            direction = 1.0 if p.side is Side.BUY else -1.0
            pnl = (cur - p.open_price) * p.volume * contract * direction
            equity += self._account_pnl(pnl, p.symbol, p.open_price)
        return equity

    def positions(self) -> List[Position]:
        return list(self._positions.values())

    def account(self) -> AccountState:
        return AccountState(
            account_id="paper", currency="USD",
            balance=self._balance, equity=self._mark_equity(),
            margin_free=self._balance, positions=self.positions(),
        )

    def symbol_specs(self, symbol: str) -> SymbolSpec:
        base, quote = parse_pair(symbol)
        point = 1e-3 if quote == "JPY" else 1e-5  # JPY pairs are 3-digit quoted
        return SymbolSpec(symbol=symbol, base_currency=base, quote_currency=quote,
                          volume_min=0.01, volume_max=100.0, volume_step=0.01,
                          digits=3 if quote == "JPY" else 5, point=point,
                          margin_currency="USD")

    def set_fx_rates(self, rates: Optional[Dict[str, float]]) -> None:
        """Account-currency value of 1 unit of each named currency
        (e.g. ``{"JPY": 1/152.5}``). The Backtester sets it from real data so
        JP¥/cross P&L is converted honestly instead of at parity."""
        self._fx = dict(rates or {})

    def _account_pnl(self, quote_pnl: float, symbol: str, price: float) -> float:
        """Quote-currency P&L → account currency (USD).

        P&L settles in the pair's quote currency. Resolution order:
          1. an explicit fx rate for the quote currency (set_fx_rates) — exact,
          2. the pair's own price when its base is the account currency
             (JP¥ P&L ÷ USDJPY = USD),
          3. parity — a ~15–30% approximation on crosses like EURGBP.
             (For JPY crosses parity is ~150× WRONG, so always prefer 1/2.)
        """
        base, quote = parse_pair(symbol)
        if quote in self._fx:
            return quote_pnl * self._fx[quote]
        if base == "USD" and quote != "USD" and price > 0:
            return quote_pnl / price
        return quote_pnl

    @property
    def closed_trades(self) -> List[tuple]:
        return list(self._closed_trades)
