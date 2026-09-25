"""Smoke test: config loads, ensemble (with choch member) builds and votes,
risk manager vetoes degenerate specs (USDSKSm) but sizes healthy ones (EURUSDm).
Offline — no broker, no orders."""
import sys
from datetime import datetime, timezone

from forexbot.config import load_settings
from forexbot.strategy import make_strategy
from forexbot.models import Action, Bar, SymbolSpec
from forexbot.risk import RiskManager
from forexbot.models import AccountState, Signal, Side


def mk_bars(n=120, base="EURUSDm", start=1.1300):
    bars = []
    ts = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    px = start
    for i in range(n):
        o = px
        c = px + (0.0004 if (i // 7) % 2 == 0 else -0.0003)
        h, l = max(o, c) + 0.0002, min(o, c) - 0.0002
        from datetime import timedelta
        ts += timedelta(minutes=15)
        bars.append(Bar(symbol=base, timestamp=ts, open=o, high=h, low=l, close=c))
        px = c
    return bars


def main():
    s = load_settings(None)  # config/default.yaml
    print(f"config OK: {len(s.symbols)} symbols, strategy={s.strategy.name}, "
          f"members={[m['name'] for m in s.strategy.params['members']]}")

    strat = make_strategy(s.strategy.name, s.strategy.params)
    print(f"ensemble OK: {len(strat.members)} members")

    bars = mk_bars()
    sig = strat.on_bar(bars, None)
    print(f"bar signal: {sig.action.value} | {sig.reason}")
    # choch is now an event member: it may HOLD (most bars) or vote with an
    # ATR stop when a confirmed swing breaks.
    choch = next(m for m in strat.members if m["strategy"].name == "choch_strategy")
    c_sig = choch["strategy"].on_bar(bars, None)
    print(f"choch alone: {c_sig.action.value} | {c_sig.reason}")
    assert c_sig.action in (Action.HOLD, Action.OPEN_LONG, Action.OPEN_SHORT)
    if c_sig.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
        assert c_sig.stop_loss is not None and c_sig.meta.get("atr")

    rm = RiskManager(s)
    acct = AccountState(account_id="test", currency="USD", balance=428.67, equity=428.67)

    skk = SymbolSpec(symbol="USDSKSm", base_currency="USD", quote_currency="SEK",
                     volume_min=1e-08, volume_max=1e-08, volume_step=1e-08,
                     digits=3, point=0.001)
    d = rm.evaluate(Signal(Action.OPEN_LONG, "USDSKSm", stop_loss=8.99),
                    acct, skk, 9.00)
    print(f"USDSKSm: approved={d.approved} reason={d.reason}")
    assert not d.approved, "degenerate spec must be vetoed"

    eur = SymbolSpec(symbol="EURUSDm", base_currency="EUR", quote_currency="USD",
                     volume_min=0.01, volume_max=200.0, volume_step=0.01,
                     digits=5, point=1e-5)
    d2 = rm.evaluate(Signal(Action.OPEN_LONG, "EURUSDm", stop_loss=1.1290),
                     acct, eur, 1.1300)
    print(f"EURUSDm: approved={d2.approved} reason={d2.reason}")
    assert d2.approved and d2.order.volume >= 0.01

    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
