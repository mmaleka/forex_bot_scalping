"""Throwaway: verify trailing-stop math + the live trail throttle (no broker I/O)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forexbot.config import Settings, TrailingConfig
from forexbot.engine import orchestrator as orch_mod
from forexbot.engine.orchestrator import Orchestrator
from forexbot.models import OrderResult, Position, Quote, Side, SymbolSpec
from forexbot.risk.trailing import TrailingStop

POINT = 1e-5
failures = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: got {got!r}" + ("" if ok else f" want {want!r}"))
    if not ok:
        failures.append(name)


def check_px(name, got, want):
    """Price comparison at broker precision (5 digits) — float addition order
    can differ by ~1e-21 between the code's path and the expected's path."""
    if got is None or want is None:
        check(name, got, want)
        return
    check(name, round(got, 5), round(want, 5))


# ── 1) TrailingStop.new_stop — pure math ──────────────────────────────────
print("1) trailing math")
t = TrailingStop(True, 20 * POINT, 15 * POINT, 5 * POINT)
entry = 1.13650

check("BUY below activation → None",
      t.new_stop(Side.BUY, entry, 1.13590, entry + 19 * POINT), None)
check_px("BUY at activation, no SL → candidate (entry+5pt)",
         t.new_stop(Side.BUY, entry, None, entry + 20 * POINT), entry + 5 * POINT)
check("BUY improvement < step → None",
      t.new_stop(Side.BUY, entry, entry + 4 * POINT, entry + 19 * POINT), None)
# improvement = (best−distance) − current_sl = 6pt ≥ step 5pt → re-place
check_px("BUY improvement ≥ step → candidate",
         t.new_stop(Side.BUY, entry, entry - POINT, entry + 20 * POINT), entry + 5 * POINT)
# exactly-at-step (improvement = 5pt) must count — the eps guard exists for this
check_px("BUY improvement == step → candidate",
         t.new_stop(Side.BUY, entry, entry, entry + 20 * POINT), entry + 5 * POINT)
check("SELL below activation → None",
      t.new_stop(Side.SELL, entry, 1.13710, entry - 19 * POINT), None)
check_px("SELL at activation → candidate (entry−5pt)",
         t.new_stop(Side.SELL, entry, 1.13710, entry - 20 * POINT), entry - 5 * POINT)
check("disabled → None",
      TrailingStop(False, 20 * POINT, 15 * POINT, 5 * POINT).new_stop(
          Side.BUY, entry, None, entry + 50 * POINT), None)

# ── 2) live trail: throttle same rejected level, accept improved one ──────
print("2) orchestrator._maybe_trail throttle + recovery")

settings = Settings(
    symbols=["EURUSDm"],
    trailing=TrailingConfig(enabled=True, activation_points=20,
                            trail_points=15, step_points=5),
)
spec = SymbolSpec(symbol="EURUSDm", base_currency="EUR", quote_currency="USD",
                  volume_min=0.01, volume_max=100.0, volume_step=0.01,
                  digits=5, point=POINT, margin_currency="USD")


class FakeBroker:
    def __init__(self):
        self.bid = entry + 131 * POINT  # well past activation
        self.pos = Position(ticket="T1", symbol="EURUSDm", side=Side.BUY,
                            volume=0.1, open_price=entry, profit=0.0,
                            stop_loss=entry - 60 * POINT)
        self.fail = True
        self.calls = []

    def positions(self):
        return [self.pos]

    def quote(self, sym):
        return Quote(symbol=sym, bid=self.bid, ask=self.bid + 2 * POINT,
                     timestamp=datetime.now(timezone.utc))

    def modify_position(self, ticket, stop_loss=None, take_profit=None):
        self.calls.append(stop_loss)
        if self.fail:
            return OrderResult(ok=False, ticket=ticket,
                               raw="[BROKER_REJECTED] Broker rejected the modify: "
                                   "trade reply timeout (msgId=999)")
        self.pos.stop_loss = stop_loss
        return OrderResult(ok=True, ticket=ticket, status="modified")


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, _s):
        pass


class FakeStore:
    def __init__(self):
        self.rows = []

    def log_trade(self, *a, **kw):
        self.rows.append(a)


broker = FakeBroker()
fake_time = FakeTime()
o = Orchestrator.__new__(Orchestrator)  # bypass __init__ (no broker/store I/O)
o.s = settings
o.broker = broker
o.store = FakeStore()
o._trail_cfg = {"EURUSDm": TrailingStop.from_settings(settings, POINT)}
o._trail_state = {}
o._spec_cache = {"EURUSDm": spec}
o._last_seen = None
real_time = orch_mod.time
orch_mod.time = fake_time
try:
    want = round(broker.bid - 15 * POINT, 5)  # best − distance

    def poll(t):
        fake_time.now = t
        o._maybe_trail("EURUSDm")

    poll(0)    # attempt 1 — rejected
    poll(5)    # same level, inside 30s cooldown → must NOT retry
    poll(29)   # still inside cooldown → must NOT retry
    check("suppressed while broker rejects (calls)", len(broker.calls), 1)
    poll(31)   # cooldown over → retry once, rejected again
    check("retried after cooldown (calls)", len(broker.calls), 2)

    broker.fail = False
    broker.bid += 10 * POINT  # best improves ≥ step → new level, sent immediately
    fake_time.now = 32        # (still inside the *next* cooldown window for level #1)
    o._maybe_trail("EURUSDm")
    check("improved level sent despite cooldown (calls)", len(broker.calls), 3)
    check("broker SL advanced", broker.pos.stop_loss, round(broker.bid - 15 * POINT, 5))
    poll(40)  # nothing new to do — candidate now < step ahead of broker SL
    check("no further spam after success (calls)", len(broker.calls), 3)

    # new position on same symbol → throttle state resets cleanly
    broker.pos = Position(ticket="T2", symbol="EURUSDm", side=Side.BUY,
                          volume=0.1, open_price=entry, profit=0.0,
                          stop_loss=entry - 60 * POINT)
    broker.fail = False
    broker.calls.clear()
    poll(41)
    check("new ticket retrails immediately (calls)", len(broker.calls), 1)
finally:
    orch_mod.time = real_time

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("all checks passed")
