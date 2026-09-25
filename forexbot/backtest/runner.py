"""Offline backtester: replays historical bars through the SAME strategy + risk
code the live engine uses, filling against the cost-aware PaperBroker.

This is the most important tool in the project — a strategy is not "ready" until
it looks honest here, with spread, slippage, and commission already paid. It also
simulates intra-bar SL/TP hits (conservative: if a bar's range crosses both the
stop and the target, the stop is assumed to fill first).
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..broker.paper import PaperBroker
from ..config import Settings
from ..models import Action, Bar, Side
from ..risk import RiskManager
from ..risk.trailing import TrailingStop
from ..strategy import make_strategy
from ..utils import tf_minutes

_MINUTES_PER_YEAR = 196_560  # ~252 trading days × ~13h of meaningful activity


@dataclass
class BacktestResult:
    start_equity: float
    end_equity: float
    net_pnl: float
    return_pct: float
    max_drawdown_pct: float
    n_trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    avg_trade_pnl: float
    sharpe: float
    trail_adjustments: int = 0
    equity_curve: List[tuple] = field(default_factory=list)
    trades: List[tuple] = field(default_factory=list)

    def summary(self) -> str:
        pf = self.profit_factor
        pf_s = f"{pf:.2f}" if math.isfinite(pf) else "inf"
        return (
            f"start       {self.start_equity:>14,.2f}\n"
            f"end         {self.end_equity:>14,.2f}\n"
            f"net PnL     {self.net_pnl:>+14,.2f}   return {self.return_pct:+8.2f}%\n"
            f"max drawdown{self.max_drawdown_pct:>13.2f}%\n"
            f"trades      {self.n_trades:>14d}   win-rate {self.win_rate:6.1f}%\n"
            f"profit fact {float('nan') if not math.isfinite(pf) else pf:>14.2f}"
            f"   avg trade {self.avg_trade_pnl:>+11,.2f}\n"
            f"sharpe(ann) {self.sharpe:>14.2f}"
            f"   trail adj. {self.trail_adjustments}"
        )


def load_bars_csv(path: str, symbol: str = "EURUSDm") -> List[Bar]:
    """Load OHLC history from a CSV with columns: timestamp,open,high,low,close."""
    bars: List[Bar] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts_raw = row.get("timestamp") or row.get("time") or row.get("datetime")
            bars.append(Bar(
                symbol=row.get("symbol", symbol) or symbol,
                timestamp=_parse_ts(ts_raw),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            ))
    bars.sort(key=lambda b: b.timestamp)
    return bars


def _parse_ts(raw: str) -> datetime:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


class Backtester:
    def __init__(self, settings: Settings, start_equity: float = 10000.0,
                 commission_per_lot: float = 7.0, slippage_points: int = 1,
                 spread_points: int = 2, fx_rates: Optional[Dict[str, float]] = None):
        self.s = settings
        self.spread_points = spread_points
        self._start_eq = start_equity
        # fx_rates: currency code -> account-currency value of 1 unit
        # (e.g. {"JPY": 1/152.5}). Used by BOTH the risk manager's sizing and
        # the paper broker's P&L conversion — without it, JPY-quoted crosses
        # are sized and marked at parity (~150× off).
        self.fx_rates = dict(fx_rates or {})
        self.paper = PaperBroker(settings, start_equity, commission_per_lot, slippage_points)
        self.paper.set_fx_rates(self.fx_rates)
        self.strategy = make_strategy(settings.strategy.name, settings.strategy.params)
        self.risk = RiskManager(settings)
        self.trailing = TrailingStop.from_settings(
            settings, point=self.paper.symbol_specs(settings.primary_symbol).point)
        self.window = 200

    def run(self, bars: List[Bar]) -> BacktestResult:
        if len(bars) < 3:
            raise ValueError("not enough bars to backtest")
        symbol = self.s.primary_symbol
        warm = max(getattr(self.strategy, "slow", 20) + 2, 50)
        spec = self.paper.symbol_specs(symbol)
        spread = self.spread_points * 1e-5

        self.paper.connect()
        bar_now = {"t": bars[0].timestamp}
        self.risk.set_clock(lambda: bar_now["t"])  # daily halt must key off bar time
        open_pos = None
        sl: Optional[float] = None
        tp: Optional[float] = None
        best: Optional[float] = None  # best price since entry (for trailing)
        trail_events = 0
        equity_curve: List[tuple] = []

        for i, bar in enumerate(bars):
            bar_now["t"] = bar.timestamp
            # Mark the market for this bar (mid ≈ close, spread split bid/ask).
            self.paper.set_market(symbol, bar.close - spread / 2, bar.close + spread / 2)

            if i < warm:
                equity_curve.append((bar.timestamp, self.paper.account().equity))
                continue

            # 1) Intra-bar stop/target check on the position open entering this bar.
            if open_pos is not None:
                hit_level = self._check_stops(bar, open_pos, sl, tp)
                if hit_level is not None:
                    self.paper.set_market(symbol, hit_level, hit_level)
                    self.paper.close(open_pos.ticket)
                    open_pos, sl, tp, best = None, None, None, None

            # 2) Decision at bar close (strategy + risk, identical to the live path).
            sig = self.strategy.on_bar(bars[i - self.window + 1: i + 1], open_pos)
            if sig.action == Action.CLOSE and open_pos is not None:
                self.paper.close(open_pos.ticket)
                open_pos, sl, tp, best = None, None, None, None
            elif sig.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
                want = Side.BUY if sig.action == Action.OPEN_LONG else Side.SELL
                if open_pos is not None and open_pos.side != want:
                    self.paper.close(open_pos.ticket)
                    open_pos, sl, tp, best = None, None, None, None
                if open_pos is None:
                    acc = self.paper.account()
                    decision = self.risk.evaluate(sig, acc, spec, bar.close,
                                                  fx=self.fx_rates or None)
                    if decision.approved and decision.order:
                        if self.paper.place(decision.order).ok:
                            open_pos = self.paper.positions()[-1]
                            sl, tp = decision.order.stop_loss, decision.order.take_profit

            # 2.5) Trailing take-profit: fold this bar's extreme into the best
            # price seen since entry, and ratchet the stop toward it. The SL
            # check (step 1) ran *before* this, so a bar that both extended the
            # trade and reversed is filled at the OLD level — conservative.
            # If the bar already closed beyond the new trail level, the trail
            # must have filled intra-bar: settle there.
            if open_pos is not None and self.trailing.enabled:
                if best is None:
                    best = open_pos.open_price  # just opened this bar
                elif open_pos.side is Side.BUY:
                    best = max(best, bar.high)
                else:
                    best = min(best, bar.low)
                new_sl = self.trailing.new_stop(open_pos.side, open_pos.open_price, sl, best)
                if new_sl is not None:
                    new_sl = round(new_sl, spec.digits)
                    sl = new_sl
                    trail_events += 1
                    beyond = ((open_pos.side is Side.BUY and bar.close <= new_sl)
                              or (open_pos.side is Side.SELL and bar.close >= new_sl))
                    if beyond:
                        self.paper.set_market(symbol, new_sl, new_sl)
                        self.paper.close(open_pos.ticket)
                        open_pos, sl, tp, best = None, None, None, None

            # 3) Mark equity at the bar close.
            equity_curve.append((bar.timestamp, self.paper.account().equity))

        return self._report(equity_curve, trail_adjustments=trail_events)

    def _check_stops(self, bar: Bar, pos, sl: Optional[float], tp: Optional[float]) -> Optional[float]:
        if sl is not None:
            if (pos.side is Side.BUY and bar.low <= sl) or (pos.side is Side.SELL and bar.high >= sl):
                return sl
        if tp is not None:
            if (pos.side is Side.BUY and bar.high >= tp) or (pos.side is Side.SELL and bar.low <= tp):
                return tp
        return None

    def _report(self, equity_curve: List[tuple], trail_adjustments: int = 0) -> BacktestResult:
        eq = [e for _, e in equity_curve]
        start, end = self._start_eq, eq[-1]
        net = end - start
        ret = net / start * 100.0 if start else 0.0

        peak, mdd = float("-inf"), 0.0
        for e in eq:
            peak = max(peak, e)
            if peak > 0:
                mdd = max(mdd, (peak - e) / peak * 100.0)

        trades = self.paper.closed_trades  # (ticket, symbol, vol, fill, pnl)
        pnls = [t[4] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        n = len(pnls)
        gross_win, gross_loss = sum(wins), -sum(losses)
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

        rets = [(eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq)) if eq[i - 1] > 0]
        sharpe = 0.0
        if len(rets) > 1:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            std = math.sqrt(var)
            if std > 0:
                sharpe = (mean / std) * math.sqrt(_MINUTES_PER_YEAR / tf_minutes(self.s.timeframe))

        return BacktestResult(
            start_equity=start, end_equity=end, net_pnl=net, return_pct=ret,
            max_drawdown_pct=mdd, n_trades=n, wins=len(wins), losses=len(losses),
            win_rate=(len(wins) / n * 100.0) if n else 0.0,
            profit_factor=profit_factor,
            avg_trade_pnl=(sum(pnls) / n) if n else 0.0,
            sharpe=sharpe, trail_adjustments=trail_adjustments,
            equity_curve=equity_curve, trades=trades,
        )


__all__ = ["Backtester", "BacktestResult", "load_bars_csv"]
