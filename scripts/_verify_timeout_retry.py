"""Verify the trade-reply-timeout retry behavior of TickerallBroker, offline.

A fake SDK client counts attempts and raises the REAL tickerall exception
classes exactly the way `map_error_response` does, so we exercise the live
_call/place/candles/modify paths without touching the network.

Covers:
  1. place(): trade reply timeout, then fill  -> ok, SAME idempotency key
     on every attempt (the de-dupe guarantee that makes the retry safe).
  2. place(): timeout on every attempt        -> ok=False after max_retries+1.
  3. place(): true rejection (INVALID_STOPS)  -> NOT retried (1 attempt).
  4. place(): ServiceUnavailable (transient)  -> still retried (regression).
  5. candles(): read after timeout            -> retried.
  6. modify_position(): after timeout         -> retried.
  7. sessions.start(): after timeout          -> NOT retried (excluded).
  8. orders.place() with NO key               -> NOT retried (no de-dupe).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import forexbot.broker.tickerall as ta  # noqa: E402
from forexbot.broker.tickerall import TickerallBroker  # noqa: E402
from forexbot.models import OrderRequest, Side  # noqa: E402
from tickerall import TickerallServiceUnavailableError  # noqa: E402
from tickerall.errors import TickerallBrokerError  # noqa: E402

ta._TRADE_TIMEOUT_RETRY_S = 0.01  # keep the test fast (we test counts, not delays)


# ── exception factories, shaped like the real broker answers ─────────────────
def trade_reply_timeout():
    return TickerallBrokerError(
        status=502, code="BROKER_REJECTED",
        message="Broker rejected the order: trade reply timeout (msgId=109795)")


def invalid_stops():
    return TickerallBrokerError(
        status=502, code="BROKER_REJECTED",
        message="Broker rejected the order: INVALID_STOPS (retcode 10016)")


def pool_draining():
    return TickerallServiceUnavailableError(
        status=503, code="POOL_SHUTTING_DOWN", message="pool draining for deploy")


# ── fake SDK client: pops one scripted behavior per call ─────────────────────
class FakeMethod:
    def __init__(self, client, name):
        self._client, self.name = client, name

    def __call__(self, *args, **kwargs):
        self._client.calls.append((self.name, args, kwargs))
        kind, payload = self._client.script.pop(0)
        if kind == "raise":
            raise payload()
        return payload


class FakeNamespace:
    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return FakeMethod(self._client, name)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.orders = FakeNamespace(self)
        self.candles = FakeNamespace(self)
        self.sessions = FakeNamespace(self)
        self.positions = FakeNamespace(self)
        self.accounts = FakeNamespace(self)


def make_broker(client, max_retries=3):
    b = object.__new__(TickerallBroker)  # skip __init__: no SDK key, no network
    b._s = None
    b._client = client
    b.account_id = "test-acc"
    b._max_retries = max_retries
    b._backoff = 0.01
    b._quote_cache = {}
    b._stream = None
    return b


def order(key="key-1"):
    return OrderRequest(symbol="USDCHFm", side=Side.BUY, volume=0.01,
                        stop_loss=0.82378, idempotency_key=key)


PASS = 0


def check(label, cond, extra=""):
    global PASS
    assert cond, f"FAIL: {label} {extra}"
    PASS += 1
    print(f"  ok  {label}")


# ── 1. timeout then fill: retried, same idempotency key throughout ───────────
print("1) place(): trade reply timeout -> retry -> fill")
c = FakeClient([("raise", trade_reply_timeout),
                ("ok", {"ticket": "999", "status": "filled"})])
res = make_broker(c).place(order())
check("succeeds on 2nd attempt", res.ok and res.ticket == "999", str(res))
check("exactly 2 attempts", len(c.calls) == 2, f"{len(c.calls)} calls")
keys = {kw.get("idempotency_key") for _, _, kw in c.calls}
check("same idempotency key on every attempt", keys == {"key-1"}, str(keys))

# ── 2. timeout every time: bounded, then surfaces as failure ─────────────────
print("2) place(): timeout on every attempt")
c = FakeClient([("raise", trade_reply_timeout)] * 4)
res = make_broker(c, max_retries=3).place(order())
check("gives up after 4 attempts", not res.ok and len(c.calls) == 4,
      f"ok={res.ok} calls={len(c.calls)}")

# ── 3. a TRUE rejection is never retried ─────────────────────────────────────
print("3) place(): INVALID_STOPS (true rejection)")
c = FakeClient([("raise", invalid_stops)])
res = make_broker(c).place(order())
check("not retried, fails fast", not res.ok and len(c.calls) == 1,
      f"calls={len(c.calls)}")

# ── 4. classic transient still retried (regression guard) ────────────────────
print("4) place(): ServiceUnavailable (transient)")
c = FakeClient([("raise", pool_draining),
                ("ok", {"ticket": "1", "status": "filled"})])
res = make_broker(c).place(order())
check("still retried", res.ok and len(c.calls) == 2, f"ok={res.ok}")

# ── 5. reads retry after timeout ─────────────────────────────────────────────
print("5) candles(): timeout then success")
row = {"timestamp": time.time(), "open": 0.8250, "high": 0.8260,
       "low": 0.8240, "close": 0.8255}
c = FakeClient([("raise", trade_reply_timeout), ("ok", [row])])
bars = make_broker(c).candles("USDCHFm", "M15", 1)
check("retried, bar returned", len(bars) == 1 and len(c.calls) == 2)

# ── 6. modify (trailing path) retries after timeout ──────────────────────────
print("6) modify_position(): timeout then success")
c = FakeClient([("raise", trade_reply_timeout), ("ok", {"status": "modified"})])
res = make_broker(c).modify_position("999", stop_loss=0.82588)
check("retried, modified", res.ok and len(c.calls) == 2, str(res))

# ── 7. sessions.start stays fail-fast (no double-session risk) ───────────────
print("7) sessions.start(): timeout NOT retried")
seen = []


def start_fn(*a, **kw):
    seen.append(1)
    raise trade_reply_timeout()


b = make_broker(FakeClient([]))
try:
    b._call("sessions.start", start_fn, "test-acc")
    raise AssertionError("expected the timeout to surface")
except TickerallBrokerError:
    pass
check("surfaces after exactly 1 attempt", len(seen) == 1, f"attempts={len(seen)}")

# ── 8. place() with no idempotency key is never retried (no de-dupe) ─────────
print("8) orders.place() without idempotency key")


def place_fn(*a, **kw):
    raise trade_reply_timeout()


seen2 = []
orig = place_fn


def counting_place(*a, **kw):
    seen2.append(1)
    return orig(*a, **kw)


b = make_broker(FakeClient([]))
try:
    b._call("orders.place", counting_place, "test-acc", idempotency_key=None)
    raise AssertionError("expected the timeout to surface")
except TickerallBrokerError:
    pass
check("not retried without a key", len(seen2) == 1, f"attempts={len(seen2)}")

print(f"\nALL {PASS} CHECKS PASSED")
