"""The risk manager — the gatekeeper.

A strategy's signal is a *wish*; the risk manager decides whether it's permitted
and, if so, exactly how big. It can veto any open, and it owns position sizing:
in ``risk`` mode (default) the size is chosen so the entry→stop distance risks
exactly ``risk.risk_per_trade_pct`` % of equity, converted to account money
via the pair's quote currency; in ``fixed`` mode it uses the flat
``risk.fixed_lot_size`` / ``risk.fixed_lot_size_jpy`` lots. Either way the size
is floored to the broker's lot step and clamped to the broker's min/max. It
also enforces the daily-loss halt and the max-concurrent-positions cap.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime
from typing import Callable, Dict, Optional

from ..config import Settings
from ..models import (
    AccountState,
    Action,
    OrderRequest,
    OrderType,
    RiskDecision,
    Side,
    Signal,
    SymbolSpec,
)
from ..utils import utcnow

log = logging.getLogger("forexbot.risk")


class RiskManager:
    def __init__(self, settings: Settings):
        self._s = settings
        self._clock: Callable[[], datetime] = utcnow
        self._day = None
        self._day_start_equity: Optional[float] = None

    def set_clock(self, clock: Callable[[], datetime]) -> None:
        """Point the daily-halt clock at another time source (the backtester
        uses the bar's timestamp — the wall clock would collapse the whole
        history into one day)."""
        self._clock = clock

    def _daily_halt(self, account: AccountState) -> bool:
        today = self._clock().date()
        if self._day != today:
            self._day = today
            self._day_start_equity = account.equity
            return False
        base = self._day_start_equity or 0.0
        if base <= 0:
            return False
        drawdown_pct = (base - account.equity) / base * 100.0
        return drawdown_pct >= self._s.risk.max_daily_loss_pct

    @staticmethod
    def _floor_to_step(v: float, step: float) -> float:
        """Round a lot size *down* to the broker step — never exceed the risk budget."""
        if step <= 0 or v <= 0:
            return v
        return round(math.floor(v / step + 1e-9) * step, 8)

    @staticmethod
    def _quote_rate(spec: SymbolSpec, entry_price: float,
                    account_currency: str, fx: Optional[Dict[str, float]]) -> float:
        """Account-money value of one unit of the pair's *quote* currency.

        A position's P&L settles in the quote currency, so sizing in account
        money needs this rate. Resolution order:
          1. an explicit ``fx`` table (the orchestrator builds one from the
             book's own quotes — exact for every pair on the default book),
          2. the pair's own price when its base is the account currency
             (USDJPY on a USD account: JPY P&L ÷ USDJPY = USD),
          3. parity — a logged approximation for crosses that don't touch the
             account currency.
        """
        quote = (spec.quote_currency or "").upper()
        acct = (account_currency or "USD").upper()
        if not quote:
            log.warning("spec for %s has no quote currency — sizing at parity", spec.symbol)
            return 1.0
        if quote == acct:
            return 1.0
        if fx and quote in fx:
            return float(fx[quote])
        if (spec.base_currency or "").upper() == acct and entry_price and entry_price > 0:
            return 1.0 / entry_price
        log.warning("no %s→%s FX rate available — sizing %s at parity; size may be off",
                    quote, acct, spec.symbol)
        return 1.0

    def evaluate(self, signal: Signal, account: AccountState,
                 spec: SymbolSpec, entry_price: float,
                 fx: Optional[Dict[str, float]] = None) -> RiskDecision:
        s = self._s.risk

        if signal.action in (Action.CLOSE, Action.HOLD):
            return RiskDecision(True, reason="non-opening action")
        if signal.action not in (Action.OPEN_LONG, Action.OPEN_SHORT):
            return RiskDecision(False, reason=f"unsupported action {signal.action}")

        if self._daily_halt(account):
            return RiskDecision(False, reason="daily loss limit reached — halting for the day")
        if account.open_position_count >= s.max_concurrent_positions:
            return RiskDecision(False,
                                reason=f"max concurrent positions ({s.max_concurrent_positions}) reached")
        # One position per pair: block a same-direction duplicate on this symbol.
        # Opposite-direction reversals are handled by the engine closing first.
        want = Side.BUY if signal.action is Action.OPEN_LONG else Side.SELL
        if sum(1 for p in account.positions if p.symbol == signal.symbol and p.side is want) \
                >= s.max_positions_per_symbol:
            return RiskDecision(False,
                                reason=f"max {s.max_positions_per_symbol} position(s) per symbol reached on {signal.symbol}")

        sl = signal.stop_loss
        if sl is None:
            return RiskDecision(False,
                                reason="no stop-loss defined; refusing to open with undefined risk")
        distance = abs(entry_price - sl)
        if distance <= 0:
            return RiskDecision(False, reason="degenerate stop distance (SL == entry)")
        # Degenerate broker spec (e.g. Exness returns min=max=step=1e-08 for
        # USDSKSm/USDDKAm) — sizing against it would clamp to a dust lot.
        if spec.volume_max < 0.001:
            return RiskDecision(
                False,
                reason=f"degenerate broker spec for {spec.symbol} "
                       f"(max volume {spec.volume_max}) — refusing to size")

        # Position sizing — see the module docstring for the two modes.
        floored = False
        if s.sizing_mode == "fixed":
            target = s.fixed_lot_size_jpy if "JPY" in signal.symbol.upper() else s.fixed_lot_size
            volume = target
            sizing_note = "fixed"
        else:
            rate = self._quote_rate(spec, entry_price, account.currency, fx)
            pnl_per_lot = distance * s.contract_size * rate  # account money, 1.0 lot over entry→SL
            risk_amount = (account.equity or 0.0) * s.risk_per_trade_pct / 100.0
            volume = risk_amount / pnl_per_lot if pnl_per_lot > 0 else 0.0
            sizing_note = f"risk {risk_amount:.2f} @ {s.risk_per_trade_pct}% eq"
        volume = self._floor_to_step(volume, spec.volume_step)
        if volume < spec.volume_min:
            if s.floor_to_min_lot:
                # Below the broker's minimum: fill at the min lot so a small
                # account still trades. The extra risk is bounded to the min
                # lot's entry→stop distance and is flagged in the reason.
                floored = True
                volume = spec.volume_min
            else:
                return RiskDecision(False,
                                    reason=f"sized volume {volume:.3f} below broker min {spec.volume_min}")
        volume = min(volume, spec.volume_max)

        side = Side.BUY if signal.action is Action.OPEN_LONG else Side.SELL
        tp = signal.take_profit
        if tp is None and s.take_profit_atr_mult > 0 and signal.meta.get("atr") is not None:
            atrv = float(signal.meta["atr"])
            tp = (entry_price + s.take_profit_atr_mult * atrv
                  if side is Side.BUY else entry_price - s.take_profit_atr_mult * atrv)
            tp = round(tp, spec.digits)
        sl = round(sl, spec.digits)

        order = OrderRequest(
            symbol=signal.symbol, side=side, volume=volume,
            order_type=OrderType.MARKET, stop_loss=sl, take_profit=tp,
            idempotency_key=str(uuid.uuid4()),
        )
        detail = f"size {volume:.2f} lots ({sizing_note}), SL {sl}" + (f", TP {tp}" if tp else "")
        if floored:
            detail += "  [size below broker min — filled at min lot, risked more than target]"
        return RiskDecision(True, order=order, reason=detail)


__all__ = ["RiskManager"]
