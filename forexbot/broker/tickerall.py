"""Tickerall execution adapter — the only vendor-specific code in the system.

Maps the normalized ``Broker`` interface onto the ``tickerall`` Python SDK
(Exness MT5 in our case), and uses two safety features the SDK offers:

  * **idempotent placement** — every order carries an ``idempotency_key`` so a
    dropped response followed by a retry can never double-fill;
  * **transient retry** — only errors flagged ``.transient`` are retried, with
    backoff. Auth/validation/404s surface immediately (retrying them is wrong);
  * **unknown-state retry** — a broker ``trade reply timeout`` is a REPLY
    timeout, not a verdict (the order may have filled), so — unlike a true
    rejection — it IS retried, under the order's idempotency key which the
    broker de-dupes (this was ~60% of failed opens on Exness).

Field access is defensive (``_get``) because the exact attribute names on
response objects can vary by SDK version; we try the likely names and fall back.
"""
from __future__ import annotations

import logging
import math
import re
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

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
from ..utils import parse_pair, utcnow
from .base import Broker

log = logging.getLogger("forexbot.broker.tickerall")

# How long a candle-derived quote stays usable when the tick stream is down.
# A live stream tick always wins (it is fresh by construction).
_QUOTE_TTL_S = 2.0

try:
    from tickerall import Tickerall, TickerallApiError, TickerallServiceUnavailableError
    _HAS_TICKERALL = True
except Exception:  # pragma: no cover - package not installed
    Tickerall = None  # type: ignore
    TickerallApiError = Exception  # type: ignore
    TickerallServiceUnavailableError = Exception  # type: ignore
    _HAS_TICKERALL = False


# ── small tolerant helpers ───────────────────────────────────────────────────
def _get(obj, *names, default=None):
    """Return the first present, non-None attribute/dict-key among ``names``."""
    for n in names:
        if obj is None:
            break
        if isinstance(obj, dict):
            v = obj.get(n)
        elif hasattr(obj, n):
            v = getattr(obj, n)
        else:
            continue
        if v is not None:
            return v
    return default


def _to_dt(row) -> datetime:
    ts = _get(row, "timestamp", "time", "datetime", default=None)
    if isinstance(ts, (int, float)):
        if ts > 1e12:  # epoch millis → seconds
            ts = ts / 1000.0
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _opt_float(v):
    """Tolerant float-or-None for optional price fields (SL/TP)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _tf_minutes(tf: str) -> int:
    """Parse a timeframe string (M1/M5/H1/D1) into minutes."""
    tf = tf.strip().upper()
    if tf.endswith("M"):
        return max(1, int(tf[:-1] or 1))
    if tf.endswith("H"):
        return int(tf[:-1] or 1) * 60
    if tf.endswith("D"):
        return int(tf[:-1] or 1) * 1440
    if tf.endswith("W"):
        return int(tf[:-1] or 1) * 10080
    return 5


# Exness answers some trades with "trade reply timeout": the matching engine
# did not answer in time. The SDK classifies it as a broker REJECTION
# (transient=False) and gives up — but the order state is UNKNOWN (it may
# have filled), which is exactly the case a retry should cover. A true
# rejection never changes on retry; this one does, and retrying is safe
# because place() always sends an idempotency key the broker de-dupes on.
_TRADE_REPLY_TIMEOUT_RE = re.compile(
    r"trade[\s_-]*reply[\s_-]*timed?[\s_-]*out", re.IGNORECASE)

# The broker is UP when it answers with this (latency, not connectivity),
# so retry on a short fixed delay instead of the reconnect backoff.
_TRADE_TIMEOUT_RETRY_S = 3.0


def _trade_timeout_retryable(name: str, kwargs: dict) -> bool:
    """Is it safe to repeat THIS call after a trade reply timeout?

    Yes when the retry is idempotent: ``orders.place`` carries an
    idempotency key (broker de-dupes), ``positions.close`` targets a ticket
    (a repeat is a no-op error, never a double close), ``*.modify`` sets an
    absolute level, and reads have no side effect. The one exception is
    ``sessions.start`` — a repeat whose first attempt actually landed leaves
    a double session, which does not self-heal — so it keeps fail-fast.
    """
    if name == "sessions.start":
        return False
    if name == "orders.place":
        return bool(kwargs.get("idempotency_key"))
    return True


class TickerallBroker(Broker):
    def __init__(self, settings: Settings, max_retries: int = 3):
        if not _HAS_TICKERALL:
            raise RuntimeError("The `tickerall` package is not installed. Run: pip install tickerall")
        self._s = settings
        self._client = Tickerall(api_key=settings.tickerall_api_key)
        self.account_id: Optional[str] = None
        self._max_retries = max_retries
        self._backoff = settings.engine.reconnect_backoff_s
        self._quote_cache: dict = {}  # symbol -> (Quote, fetch-time); stream tick or candle fallback
        self._stream = None  # tickerall TickerallStream once start_stream() runs

    # ── lifecycle ──────────────────────────────────────────────────────────
    def connect(self) -> AccountState:
        b = self._s.broker
        if b.keep_alive:
            # ONE call that starts the session AND registers it for the SDK's
            # transparent auto re-arm: if the account later goes cold, the
            # client re-arms and retries in place. (Starting a session first
            # and calling keep_alive afterwards would register the re-arm
            # under a DIFFERENT account id, so NOT_HOT errors would never heal.)
            session = self._call(
                "sessions.keep_alive", self._client.sessions.keep_alive,
                broker=b.broker, server=b.server, account=b.account,
                password=b.password, terminal_type=b.terminal_type,
            )
        else:
            session = self._call(
                "sessions.start", self._client.sessions.start,
                broker=b.broker, server=b.server, account=b.account,
                password=b.password, terminal_type=b.terminal_type,
            )
        self.account_id = str(_get(session, "account_id", "id", default=""))
        log.info("connected to account %s (mode=%s, keep_alive=%s, demo=%s)",
                 self.account_id, self._s.mode, b.keep_alive,
                 _get(session, "is_demo", default=""))
        return self.account()

    def disconnect(self) -> None:
        self.stop_stream()
        if self.account_id:
            try:
                self._client.sessions.end(self.account_id)
            except Exception as e:
                log.warning("sessions.end failed: %s", e)
            self.account_id = None

    # ── market data ────────────────────────────────────────────────────────
    def quote(self, symbol: str) -> Quote:
        # 1) Cached quote (stream tick or candle fallback), short-TTL so the
        #    engine's 1s trail polls don't stampede the REST API — and so a
        #    QUIET stream degrades to the (fresh) candle fallback instead of
        #    handing back a stale tick. (An unbounded cache was the old bug:
        #    the "best price" for trailing froze at the first bar close and
        #    never moved.)
        cached = self._quote_cache.get(symbol)
        if cached is not None:
            q, fetched = cached
            if (utcnow() - fetched).total_seconds() < _QUOTE_TTL_S:
                return q
        # 2) Fallback: latest candle close + synthetic spread (last resort).
        bars = self.candles(symbol, self._s.timeframe, 1)
        last = bars[-1]
        spread = last.close * 2e-5  # ~2 pips, a last-resort assumption only
        q = Quote(symbol=symbol, bid=last.close, ask=last.close + spread, timestamp=last.timestamp)
        self._quote_cache[symbol] = (q, utcnow())
        return q

    def candles(self, symbol: str, timeframe: str, count: int) -> List[Bar]:
        hours = max(1, math.ceil(count * _tf_minutes(timeframe) / 60.0) + 1)
        rows = self._call(
            "candles.get", self._client.candles.get,
            account_id=self.account_id, symbol=symbol, hours=hours, timeframe=timeframe,
        ) or []
        bars = [
            Bar(
                symbol=symbol,
                timestamp=_to_dt(r),
                open=float(_get(r, "open", default=0.0)),
                high=float(_get(r, "high", default=0.0)),
                low=float(_get(r, "low", default=0.0)),
                close=float(_get(r, "close", default=0.0)),
            )
            for r in rows
        ]
        return bars[-count:] if count else bars

    # ── trading ────────────────────────────────────────────────────────────
    def place(self, order: OrderRequest) -> OrderResult:
        kwargs = dict(
            type=order.order_type.value,
            symbol=order.symbol,
            side=order.side.value,
            volume=order.volume,
            idempotency_key=order.idempotency_key or str(uuid.uuid4()),
        )
        if order.stop_loss is not None:
            kwargs["stop_loss"] = order.stop_loss
        if order.take_profit is not None:
            kwargs["take_profit"] = order.take_profit
        if order.price is not None:
            kwargs["price"] = order.price
        try:
            res = self._call("orders.place", self._client.orders.place, self.account_id, **kwargs)
            return OrderResult(ok=True,
                               ticket=str(_get(res, "ticket", default="")),
                               status=str(_get(res, "status", default="")),
                               raw=res)
        except Exception as e:
            log.error("order placement failed: %s", e)
            return OrderResult(ok=False, raw=e)

    def close(self, ticket: str, volume: Optional[float] = None) -> OrderResult:
        kwargs = {"volume": volume} if volume else {}
        try:
            res = self._call("positions.close", self._client.positions.close,
                             self.account_id, ticket, **kwargs)
            return OrderResult(ok=True, ticket=ticket, status="closed", raw=res)
        except Exception as e:
            log.error("position close failed: %s", e)
            return OrderResult(ok=False, raw=e)

    def modify_position(self, ticket: str, stop_loss: Optional[float] = None,
                        take_profit: Optional[float] = None) -> OrderResult:
        """Move SL/TP on an open position (the trailing-TP application path).

        Tries the SDK's likely endpoints in order; the trailing engine treats
        a failure as non-fatal and keeps the stop where it is — so the failure
        reason must be *accurate*: report the actual last error, never a
        generic fallback (an earlier version said "no modify endpoint
        available" even when the call was made and the broker rejected it,
        which masked broker congestion as an SDK gap).
        """
        kwargs = {}
        if stop_loss is not None:
            kwargs["stop_loss"] = stop_loss
        if take_profit is not None:
            kwargs["take_profit"] = take_profit
        last_error: Optional[Exception] = None
        for endpoint in ("positions", "orders"):
            mod = getattr(getattr(self._client, endpoint, None), "modify", None)
            if mod is None:
                continue
            try:
                res = self._call(f"{endpoint}.modify", mod, self.account_id, ticket, **kwargs)
                return OrderResult(ok=True, ticket=ticket, status="modified", raw=res)
            except Exception as e:
                log.warning("%s.modify failed: %s", endpoint, e)
                last_error = e
        reason = str(last_error) if last_error else "no modify endpoint available"
        return OrderResult(ok=False, ticket=ticket, raw=reason)

    # ── state ──────────────────────────────────────────────────────────────
    def positions(self) -> List[Position]:
        acc = self._call("accounts.get", self._client.accounts.get, self.account_id)
        out = []
        for p in (_get(acc, "positions", default=[]) or []):
            out.append(Position(
                ticket=str(_get(p, "ticket", default="")),
                symbol=str(_get(p, "symbol", default="")),
                side=Side(str(_get(p, "side", default="BUY"))),
                volume=float(_get(p, "volume", default=0.0)),
                open_price=float(_get(p, "entry_price", "open_price", "open", "price", default=0.0)),
                profit=float(_get(p, "profit", default=0.0)),
                stop_loss=_opt_float(_get(p, "stop_loss", "sl", default=None)),
                take_profit=_opt_float(_get(p, "take_profit", "tp", default=None)),
            ))
        return out

    def account(self) -> AccountState:
        # The SDK's AccountDetail carries the financials NESTED under `.account`
        # (an AccountInfo) — top-level only has id/broker/status/positions.
        detail = self._call("accounts.get", self._client.accounts.get, self.account_id)
        info = _get(detail, "account", default=None)
        return AccountState(
            account_id=str(_get(detail, "id", "account_id", default=self.account_id or "")),
            currency=str(_get(info, "currency", default=None) or "USD"),
            balance=float(_get(info, "balance", default=0.0)),
            equity=float(_get(info, "equity", "balance", default=0.0)),
            margin_free=float(_get(info, "free_margin", "margin_free", default=0.0)),
            positions=self.positions(),
        )

    def symbol_specs(self, symbol: str) -> SymbolSpec:
        specs = self._call("accounts.symbol_specs", self._client.accounts.symbol_specs,
                           self.account_id)
        if isinstance(specs, list):
            # SDK SymbolSpec keys the pair by `.name` (not `.symbol`).
            spec = next((s for s in specs if str(_get(s, "name", "symbol", default="")) == symbol),
                        specs[0] if specs else None)
        elif isinstance(specs, dict):
            spec = specs.get(symbol, specs)
        else:
            spec = specs

        def f(*names, default):
            v = _get(spec, *names, default=None)
            return float(v) if v is not None else default

        # The SDK's SymbolSpec omits the currency legs — derive them from the
        # pair name (needed to build the account-currency FX table for sizing).
        base = str(_get(spec, "base", "base_currency", default=""))
        quote = str(_get(spec, "quote", "quote_currency", default=""))
        if not base or not quote:
            base, quote = parse_pair(symbol)

        return SymbolSpec(
            symbol=symbol,
            base_currency=base,
            quote_currency=quote,
            volume_min=f("volume_min", "min_volume", default=0.01),
            volume_max=f("volume_max", "max_volume", default=100.0),
            volume_step=f("volume_step", "step", default=0.01),
            digits=int(_get(spec, "digits", default=5)),
            point=float(_get(spec, "point", default=1e-5)),
            margin_currency=str(_get(spec, "margin_currency", "currency", default="")),
            spread_typical=f("spread", "spread_typical", default=0.0),
        )

    # ── retry wrapper ──────────────────────────────────────────────────────
    def _call(self, name, fn, *args, **kwargs):
        last: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except TickerallServiceUnavailableError as e:
                last = e
                log.warning("%s: transient, retry %d/%d", name, attempt + 1, self._max_retries + 1)
            except TickerallApiError as e:
                if getattr(e, "transient", False):
                    last = e
                    log.warning("%s: transient, retry %d/%d", name, attempt + 1, self._max_retries + 1)
                elif _TRADE_REPLY_TIMEOUT_RE.search(str(e)) and _trade_timeout_retryable(name, kwargs):
                    # Unknown state, not a verdict — retry under the same
                    # idempotency key so the broker de-dupes, never double-fills.
                    last = e
                    log.warning(
                        "%s: trade reply timeout (unknown state — may have filled), "
                        "retry %d/%d under same idempotency key",
                        name, attempt + 1, self._max_retries + 1)
                else:
                    raise
            if attempt < self._max_retries:
                # Trade-reply timeouts: broker is up and answering — short flat
                # delay. Genuine transients: linear reconnect backoff.
                if _TRADE_REPLY_TIMEOUT_RE.search(str(last)):
                    time.sleep(_TRADE_TIMEOUT_RETRY_S)
                else:
                    time.sleep(self._backoff * (attempt + 1))
        if last is not None:
            raise last
        raise RuntimeError(f"{name}: retry loop exited without a result")

    # ── optional tick stream (low-latency quotes + position updates) ───────
    def start_stream(self, symbols: List[str]) -> None:
        if self._stream is not None:
            return
        # client.stream is the StreamNamespace FACTORY — .connect() returns the
        # live TickerallStream that owns .on()/.subscribe_ticks()/.latest_tick().
        st = self._client.stream.connect()

        def on_tick(e):
            # Cache as (Quote, arrival-time) — the same shape quote() reads —
            # so its TTL check degrades a quiet/dead stream to the candle
            # fallback instead of handing back a stale price.
            q = Quote(symbol=str(e.symbol), bid=float(e.bid), ask=float(e.ask),
                      timestamp=datetime.now(timezone.utc))
            self._quote_cache[str(e.symbol)] = (q, utcnow())

        st.on("tick", on_tick)
        st.on("position", lambda e: log.info("position event: %s", getattr(e, "event", None)))
        st.subscribe_ticks(self.account_id, symbols)
        st.subscribe_positions(self.account_id)
        self._stream = st
        log.info("tick stream started for %s", symbols)

    def stop_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
