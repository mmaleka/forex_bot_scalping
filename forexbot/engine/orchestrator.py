"""The orchestrator — the live/paper event loop that ties the system together.

Per new closed bar:  feed -strategy.on_bar -risk.evaluate -(close opposite) -broker.place
Per loop iteration:  reconcile broker-side closes (SL/TP) and persist the equity curve.

The broker owns SL/TP, so positions can vanish without us acting — `_reconcile`
diffs the position set between loops and logs anything that disappeared, so the
trade log and equity history stay complete even for stop-outs.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from ..broker import make_broker
from ..config import Settings
from ..data import BarFeed
from ..models import Action, Position, Side, SymbolSpec
from ..observe import setup_logging
from ..risk import RiskManager
from ..risk.trailing import TrailingStop
from ..store import Store
from ..strategy import make_strategy

log = logging.getLogger("forexbot.engine")

try:  # Windows-safe signal handling (only works from the main thread)
    import signal as _signal
    _HAS_SIGNAL = True
except Exception:  # pragma: no cover
    _HAS_SIGNAL = False

# Backoff before re-sending a trail level the broker just rejected (e.g. an
# Exness "trade reply timeout" window). An *improved* level is never delayed;
# this only stops re-hammering a congested broker with the same request every
# poll, which made the congestion worse.
_TRAIL_RETRY_COOLDOWN_S = 30.0


class Orchestrator:
    def __init__(self, settings: Settings, db_path: str = "data/bot.sqlite3"):
        self.s = settings
        setup_logging(settings.logging.level, settings.logging.file)
        self.broker = make_broker(settings)
        self.strategy = make_strategy(settings.strategy.name, settings.strategy.params)
        self.risk = RiskManager(settings)
        self.store = Store(db_path)
        self.symbols = list(settings.symbols)
        # One independent feed per pair. A single strategy instance serves all of
        # them — strategies are pure (the symbol comes from the bars they read).
        self.feeds = {
            sym: BarFeed(self.broker, sym, settings.timeframe, warmup=250)
            for sym in self.symbols
        }
        self._stop = False
        self._last_seen = None  # ticket -> Position, last broker position snapshot
        # Trailing take-profit state, keyed per symbol (see _maybe_trail):
        self._trail_cfg: Dict[str, TrailingStop] = {}
        self._trail_state: Dict[str, Dict[str, object]] = {}
        self._spec_cache: Dict[str, SymbolSpec] = {}  # symbol_specs is a live API call — cache it

    def _request_stop(self, *args):
        log.info("shutdown requested")
        self._stop = True

    def run(self):
        if _HAS_SIGNAL:
            try:
                _signal.signal(_signal.SIGINT, self._request_stop)
                _signal.signal(_signal.SIGTERM, self._request_stop)
            except (ValueError, OSError):
                pass

        log.info("starting | mode=%s symbols=%s tf=%s strategy=%s",
                 self.s.mode, ",".join(self.symbols), self.s.timeframe, self.s.strategy.name)
        acc = self.broker.connect()
        log.info("connected: account=%s equity=%.2f balance=%.2f",
                 acc.account_id, acc.equity, acc.balance)
        # Trailing config per symbol (point value can differ per pair).
        for sym in self.symbols:
            self._trail_cfg[sym] = TrailingStop.from_settings(self.s, self._spec(sym).point)
        if self._trail_cfg and self._trail_cfg[self.symbols[0]].enabled:
            t = self._trail_cfg[self.symbols[0]]
            pt = self._spec(self.symbols[0]).point
            log.info("trailing TP on: activate @%.0f pts, trail %.0f pts, step %.0f pts",
                     t.activation / pt, t.distance / pt, t.step / pt)

        if self.s.engine.stream and hasattr(self.broker, "start_stream"):
            try:
                self.broker.start_stream(self.symbols)
            except Exception as e:
                log.warning("tick stream unavailable; using candle polling: %s", e)

        for sym in self.symbols:
            if not self.feeds[sym].warm():
                log.warning("feed %s not warm yet — will start trading %s once it is", sym, sym)

        try:
            self._loop()
        finally:
            self._shutdown()

    def _loop(self):
        interval = max(0.2, self.s.engine.poll_interval_s)
        while not self._stop:
            try:
                for sym in self.symbols:
                    new = self.feeds[sym].poll()
                    if new:
                        self._on_new_bar(sym, new[-1])
                self._reconcile()
                for sym in self.symbols:
                    self._maybe_trail(sym)
                self._snapshot()
            except Exception as e:
                log.exception("loop error (continuing): %s", e)
            time.sleep(interval)

    # ── per-bar decision ───────────────────────────────────────────────────
    def _current_position(self, symbol: str) -> Optional[Position]:
        for p in self.broker.positions():
            if p.symbol == symbol:
                return p
        return None

    def _spec(self, sym: str) -> SymbolSpec:
        sp = self._spec_cache.get(sym)
        if sp is None:
            sp = self.broker.symbol_specs(sym)
            self._spec_cache[sym] = sp
        return sp

    def _fx_rates(self, account_currency: str) -> Dict[str, float]:
        """Account-money value of one unit of each book currency.

        Every book pair quoted against the account currency contributes its
        rate directly (EURUSD → EUR, GBPUSD → GBP, USDJPY → JPY, …). On the
        default 10-pair book that covers every currency, so cross pairs
        (EURGBP, EURJPY, GBPJPY) size in true account money. Pairs we can't
        price are left out; the risk manager then falls back.
        """
        acct = (account_currency or "USD").upper()
        rates: Dict[str, float] = {acct: 1.0}
        for sym in self.symbols:
            try:
                sp = self._spec(sym)
                q = self.broker.quote(sym)
            except Exception:
                continue
            if q is None or q.mid <= 0:
                continue
            base, quote = (sp.base_currency or "").upper(), (sp.quote_currency or "").upper()
            if base == acct and quote:
                rates[quote] = 1.0 / q.mid
            elif quote == acct and base:
                rates[base] = q.mid
        return rates

    def _on_new_bar(self, symbol: str, bar):
        window = self.feeds[symbol].window(200)
        pos = self._current_position(symbol)
        sig = self.strategy.on_bar(window, pos)
        log.info("bar %s %s close=%.5f -%s (%s)",
                 symbol, bar.timestamp.isoformat(), bar.close, sig.action.value, sig.reason)

        if sig.action == Action.HOLD:
            return
        if sig.action == Action.CLOSE:
            self._close_position(symbol, "strategy close")
            return

        # OPEN_LONG / OPEN_SHORT
        want_side = Side.BUY if sig.action == Action.OPEN_LONG else Side.SELL

        held = self._current_position(symbol)
        if held is not None:
            if held.side == want_side:
                # One position per pair: already in this direction — hold.
                log.info("%s already %s — holding (one position per pair)", symbol, want_side.value)
                return
            self._close_position(symbol, "reverse")

        quote = self.broker.quote(symbol)
        entry = quote.ask if want_side is Side.BUY else quote.bid
        spec = self._spec(symbol)

        acc = self.broker.account()
        decision = self.risk.evaluate(sig, acc, spec, entry,
                                      fx=self._fx_rates(acc.currency))
        if not decision.approved:
            log.info("RISK VETO [%s]: %s", symbol, decision.reason)
            self.store.log_trade("vetoed", symbol, None, None, None, None, None,
                                 decision.reason)
            return

        order = decision.order
        log.info("ORDER: %s %s %.2f lots SL=%s TP=%s (%s)",
                 order.side.value, order.symbol, order.volume,
                 order.stop_loss, order.take_profit, decision.reason)
        res = self.broker.place(order)
        if res.ok:
            self.store.log_trade("open", order.symbol, order.side.value, order.volume,
                                 entry, res.ticket, None, decision.reason)
            log.info("filled ticket=%s", res.ticket)
        else:
            self.store.log_trade("open_failed", order.symbol, order.side.value, order.volume,
                                 entry, None, None, str(res.raw))
            log.error("order placement failed: %s", res.raw)

    def _close_position(self, symbol: str, reason: str):
        pos = self._current_position(symbol)
        if pos is None:
            return
        res = self.broker.close(pos.ticket)
        if res.ok:
            self.store.log_trade("close", pos.symbol, pos.side.value, pos.volume,
                                 pos.open_price, pos.ticket, pos.profit, reason)
            if self._last_seen is not None:
                self._last_seen.pop(pos.ticket, None)
            self._trail_state[symbol] = {"ticket": None, "best": None, "sl": None}
            log.info("closed %s (%s)", pos.ticket, reason)
        else:
            log.error("close failed: %s", res.raw)

    def _maybe_trail(self, symbol: str):
        """Trailing take-profit, applied live per pair: as price runs in our
        favor the broker-side stop is ratcheted toward it (via
        ``modify_position``), so a runner can never all the way back through
        entry once activated.

        Runs every poll (tick-driven, not just on bar close). If the broker
        can't modify, the stop simply stays where it is — trailing is a
        profit-lock enhancement, never the only stop.
        """
        trail = self._trail_cfg.get(symbol)
        if trail is None or not trail.enabled:
            return
        state = self._trail_state.setdefault(
            symbol, {"ticket": None, "best": None, "sl": None,
                     "attempted": None, "attempted_at": 0.0})
        pos = self._current_position(symbol)
        if pos is None:
            state["ticket"], state["best"], state["sl"] = None, None, None
            state["attempted"] = None
            return

        if state["ticket"] != pos.ticket:  # new position: restart the trail
            state["ticket"] = pos.ticket
            state["best"] = None
            state["sl"] = pos.stop_loss  # broker-reported SL, if any
            state["attempted"], state["attempted_at"] = None, 0.0

        try:
            q = self.broker.quote(pos.symbol)
        except Exception as e:
            log.warning("trail: quote failed (%s); skipping this poll", e)
            return
        # The price we could actually exit at: bid for longs, ask for shorts.
        mark = q.bid if pos.side is Side.BUY else q.ask
        if state["best"] is None:
            state["best"] = mark
        elif pos.side is Side.BUY:
            state["best"] = max(state["best"], mark)
        else:
            state["best"] = min(state["best"], mark)

        new_sl = trail.new_stop(pos.side, pos.open_price, state["sl"], state["best"])
        if new_sl is None:
            return
        new_sl = round(new_sl, self._spec(pos.symbol).digits)

        # Same level as a recent failed attempt → the broker already said no;
        # wait out the cooldown instead of resending every poll. A better
        # level (best price moved ≥ step) is sent right away.
        attempted = state.get("attempted")
        if attempted is not None and abs(attempted - new_sl) <= 1e-9 \
                and time.monotonic() - float(state.get("attempted_at", 0.0)) < _TRAIL_RETRY_COOLDOWN_S:
            return
        state["attempted"], state["attempted_at"] = new_sl, time.monotonic()

        res = self.broker.modify_position(pos.ticket, stop_loss=new_sl)
        if res.ok:
            state["sl"] = new_sl
            state["attempted"] = None
            self.store.log_trade("trail", pos.symbol, pos.side.value, pos.volume,
                                 new_sl, pos.ticket, None,
                                 f"trailing stop -{new_sl} (best {state['best']:.5f})")
            log.info("trailed stop %s -%.5f (best %.5f)",
                     pos.ticket, new_sl, state["best"])
        else:
            log.warning("trail modify failed — stop unchanged (%s)", res.raw)

    def _reconcile(self):
        """Log positions the broker closed on its own (SL/TP/external)."""
        current = {p.ticket: p for p in self.broker.positions()}
        if self._last_seen is not None:
            for ticket, p in self._last_seen.items():
                if ticket not in current:
                    self.store.log_trade("closed_by_broker", p.symbol, p.side.value,
                                         p.volume, p.open_price, ticket, p.profit,
                                         "SL/TP or external")
                    log.info("broker closed %s (pnl≈%.2f)", ticket, p.profit)
        self._last_seen = current

    def _snapshot(self):
        acc = self.broker.account()
        self.store.log_equity(acc.equity, acc.balance, acc.open_position_count)

    def _shutdown(self):
        log.info("shutting down…")
        try:
            if hasattr(self.broker, "stop_stream"):
                self.broker.stop_stream()
            self._snapshot()
            self.broker.disconnect()
        except Exception as e:
            log.warning("shutdown error: %s", e)
        log.info("bye")


__all__ = ["Orchestrator"]
