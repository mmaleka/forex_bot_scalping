"""A/B proof: JPY-quoted cross pairs (GBPJPY/CADJPY/…) have their P&L converted
at PARITY in PaperBroker._account_pnl (1 JPY = 1 USD), overstating results
~USDJPY× (~150×).

The conversion rule in broker/paper.py:
    if base == "USD" and quote != "USD": return quote_pnl / price   # correct for USDJPY
    return quote_pnl                                                # JPY P&L treated as USD!

This script re-runs each JPY-cross H1 backtest twice: once as-is (the bug),
once with JPY P&L converted at the AVERAGE USDJPY from our cached USDJPYm data
(what the broker would actually do). If the "corrected" numbers collapse into
the same league as the properly-priced pairs, the bug is confirmed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run without pip install

from forexbot.backtest import Backtester, load_bars_csv
from forexbot.broker.paper import PaperBroker
from forexbot.config import load_settings
from forexbot.utils import parse_pair

JPY_CROSSES = ["GBPJPYm", "CADJPYm", "AUDJPYm", "NZDJPYm"]
DATA = Path("data/h1")


def avg_usdjpy() -> float:
    bars = load_bars_csv(str(DATA / "USDJPYm.h1.csv"), "USDJPYm")
    closes = [b.close for b in bars]
    return sum(closes) / len(closes)


def main():
    usdjpy = avg_usdjpy()
    print(f"average USDJPY over the backtest window: {usdjpy:.1f}\n")

    s = load_settings(None)
    s.timeframe = "H1"
    orig = PaperBroker._account_pnl

    def corrected(self, quote_pnl, symbol, price):
        base, quote = parse_pair(symbol)
        if quote == "JPY" and base != "USD":
            return quote_pnl / usdjpy
        return orig(self, quote_pnl, symbol, price)

    header = f"{'symbol':<10}{'return (as-is)':>16}{'return (corrected)':>20}{'avg trade (corr.)':>20}{'n':>6}"
    print(header)
    print("─" * len(header))
    for sym in JPY_CROSSES:
        bars = load_bars_csv(str(DATA / f"{sym}.h1.csv"), sym)

        s.symbols = [sym]
        PaperBroker._account_pnl = orig
        res_buggy = Backtester(s, start_equity=10000.0).run(bars)

        s.symbols = [sym]
        PaperBroker._account_pnl = corrected
        res_fixed = Backtester(s, start_equity=10000.0).run(bars)
        PaperBroker._account_pnl = orig

        print(f"{sym:<10}{res_buggy.return_pct:>+15.2f}%{res_fixed.return_pct:>+19.2f}%"
              f"{res_fixed.avg_trade_pnl:>+20.2f}{res_fixed.n_trades:>6d}")
        print(f"{'':<10}   trades: {res_buggy.n_trades} → {res_fixed.n_trades}  "
              f"PF: {res_buggy.profit_factor:.2f} → {res_fixed.profit_factor:.2f}  "
              f"(trade count/PF unchanged — only the currency conversion differs)")

    print(f"\nIf 'corrected' lands in the +2%…+6% league of the other pairs, "
          f"the parity bug is confirmed.")


if __name__ == "__main__":
    main()
