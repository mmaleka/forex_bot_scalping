"""Verify the min-lot floor: a sub-minimum size now fills at the broker min
instead of being vetoed, and the strict path (floor_to_min_lot=false) still vetoes.

Uses the REAL account equity (~436) so the risk-based size genuinely lands
below the 0.01 broker minimum — the case that was starving the bot of fills.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forexbot.config import load_settings
from forexbot.models import AccountState, Action, Signal, SymbolSpec
from forexbot.risk import RiskManager
from forexbot.utils import parse_pair

s = load_settings(None)
print(f"floor_to_min_lot = {s.risk.floor_to_min_lot}  |  risk_per_trade_pct = {s.risk.risk_per_trade_pct}")

# Real demo-account equity (the size that used to get vetoed).
eq = AccountState("t", "USD", 436.17, 436.17)


def spec(sym):
    b, q = parse_pair(sym)
    return SymbolSpec(symbol=sym, base_currency=b, quote_currency=q,
                      volume_min=0.01, volume_max=100.0, volume_step=0.01)


def try_order(sym, entry, sl, floor):
    s.risk.floor_to_min_lot = floor
    rm = RiskManager(s)
    sig = Signal(action=Action.OPEN_LONG, symbol=sym, stop_loss=sl)
    d = rm.evaluate(sig, eq, spec(sym), entry)
    if d.approved:
        return f"APPROVED  {d.order.volume:.3f} lots  | {d.reason}"
    return f"VETOED    | {d.reason}"


# USDJPY, 40-pip stop: at $436 / 0.5% the risk-based size is ~0.0086 lots
# (below the 0.01 min) → this is exactly the case that was being vetoed.
print("\nUSDJPm 40-pip stop @ 158.00 → 157.60 (size ~0.0086 lots, below 0.01 min):")
print("  floor=TRUE :", try_order("USDJPYm", 158.0, 157.6, True))
print("  floor=FALSE:", try_order("USDJPYm", 158.0, 157.6, False))

# A calm bar where the size already clears the min — should be unchanged.
print("\nEURUSDm 7-pip stop @ 1.1380 → 1.1373 (size ~0.03 lots, above min):")
print("  floor=TRUE :", try_order("EURUSDm", 1.1380, 1.1373, True))
