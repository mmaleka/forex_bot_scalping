"""Throwaway: verify risk-based sizing math + backtest A/B (risk vs fixed)."""
import logging
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forexbot.config import load_settings
from forexbot.models import AccountState, Action, Bar, Signal, SymbolSpec
from forexbot.risk import RiskManager
from forexbot.utils import parse_pair as _parse_pair
from forexbot.backtest import Backtester, load_bars_csv

logging.basicConfig(level=logging.WARNING)

s = load_settings(None)
print("sizing_mode default =", s.risk.sizing_mode, "| risk_per_trade_pct =", s.risk.risk_per_trade_pct)

# ── 1) sizing math (equity $10k, 0.5% = $50 at risk) ─────────────────────
rm = RiskManager(s)
eq = AccountState("t", "USD", 10000, 10000)


def spec(sym):
    b, q = _parse_pair(sym)
    return SymbolSpec(symbol=sym, base_currency=b, quote_currency=q,
                      volume_min=0.01, volume_max=100.0, volume_step=0.01)


def vol(symbol, entry, sl, fx=None):
    sig = Signal(action=Action.OPEN_LONG, symbol=symbol, stop_loss=sl)
    d = rm.evaluate(sig, eq, spec(symbol), entry, fx=fx)
    return (f"{d.order.volume:.2f} lots" if d.approved
            else f"VETOED: {d.reason}")


print("\nEURUSD 16-pip SL @1.1800:", vol("EURUSDm", 1.1800, 1.1784), " (expect 0.31)")
print("USDJPY 30-pip SL @155.00:", vol("USDJPYm", 155.0, 155.30), " (expect 0.25)")
print("GBPJPY 20-pip SL @210.00 (parity fallback):", vol("GBPJPYm", 210.0, 209.80))
print("GBPJPY 20-pip SL w/ fx table:", vol("GBPJPYm", 210.0, 209.80,
                                          fx={"USD": 1.0, "JPY": 1 / 155.0}), " (expect 0.38)")

# ── 2) A/B on real EURUSD data: M5 (raw) and M15 (resampled) ─────────────
bars5 = load_bars_csv("data/yf_history.csv")


def resample(bars, minutes):
    step = timedelta(minutes=minutes)
    out, cur_key, agg = [], None, None
    for b in bars:
        key = b.timestamp.replace(minute=(b.timestamp.minute // minutes) * minutes, second=0)
        if key != cur_key:
            if agg:
                out.append(Bar(b.symbol, cur_key, agg["o"], agg["h"], agg["l"], agg["c"], 0.0))
            cur_key, agg = key, {"o": b.open, "h": b.high, "l": b.low, "c": b.close}
        else:
            agg["h"] = max(agg["h"], b.high)
            agg["l"] = min(agg["l"], b.low)
            agg["c"] = b.close
    if agg:
        out.append(Bar(bars[0].symbol, cur_key, agg["o"], agg["h"], agg["l"], agg["c"], 0.0))
    return out


for label, bars in (("M5 ", bars5), ("M15", resample(bars5, 15))):
    print(f"\n── {label}: {len(bars)} bars {bars[0].timestamp:%Y-%m-%d} → {bars[-1].timestamp:%Y-%m-%d}")
    for mode in ("risk", "fixed"):
        s.risk.sizing_mode = mode
        bt = Backtester(s, start_equity=10000)
        r = bt.run(bars)
        pf = f"{r.profit_factor:.2f}"
        worst = min(t[4] for t in r.trades) if r.trades else 0.0
        print(f"[{mode:5}] trades {r.n_trades:3d}  ret {r.return_pct:+7.2f}%  "
              f"maxDD {r.max_drawdown_pct:5.2f}%  PF {pf:>5}  avg {r.avg_trade_pnl:+7.2f}  "
              f"worst {worst:+8.2f}")
