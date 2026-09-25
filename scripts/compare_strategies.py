"""Head-to-head: each strategy standalone vs the ensemble, on the same bars.

Usage:  python scripts/compare_strategies.py [--bars data/sample.csv] [--equity 10000]
"""
from __future__ import annotations

import argparse
import math

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run without pip install

from forexbot.backtest import Backtester, load_bars_csv
from forexbot.config import StrategyConfig, load_settings

# The 4 standalone strategies + the ensemble that votes among them.
CANDIDATES = [
    ("ma_cross", "ma_cross", {"fast": 20, "slow": 50}),
    ("donchian_breakout", "donchian_breakout", {"lookback": 20}),
    ("rsi_reversion", "rsi_reversion", {"period": 14, "oversold": 30, "overbought": 70}),
    ("bollinger_reversion", "bollinger_reversion", {"period": 20, "num_std": 2.0}),
    ("ENSEMBLE (4-way vote)", "ensemble", {
        "min_votes": 2,
        "min_close_votes": 2,
        "members": [
            {"name": "ma_cross", "params": {"fast": 20, "slow": 50}, "weight": 1.0},
            {"name": "donchian_breakout", "params": {"lookback": 20}, "weight": 1.0},
            {"name": "rsi_reversion", "params": {"period": 14, "oversold": 30, "overbought": 70}, "weight": 1.0},
            {"name": "bollinger_reversion", "params": {"period": 20, "num_std": 2.0}, "weight": 1.0},
        ],
    }),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bars", default="data/sample.csv")
    p.add_argument("--equity", type=float, default=10000.0)
    p.add_argument("--spread", type=int, default=2)
    p.add_argument("--slippage", type=int, default=1)
    args = p.parse_args()

    s = load_settings(None)
    bars = load_bars_csv(args.bars, s.primary_symbol)
    print(f"{len(bars)} bars of {s.primary_symbol}  "
          f"({bars[0].timestamp:%Y-%m-%d} → {bars[-1].timestamp:%Y-%m-%d})\n")

    header = (f"{'strategy':<24}{'return':>9}{'maxDD':>8}{'trades':>8}"
              f"{'win%':>7}{'PF':>7}{'sharpe':>8}{'avg trade':>12}")
    print(header)
    print("─" * len(header))
    for label, name, params in CANDIDATES:
        s.strategy = StrategyConfig(name=name, params=params)
        bt = Backtester(s, start_equity=args.equity,
                        commission_per_lot=7.0, slippage_points=args.slippage,
                        spread_points=args.spread)
        res = bt.run(bars)
        pf = f"{res.profit_factor:.2f}" if math.isfinite(res.profit_factor) else "inf"
        print(f"{label:<24}{res.return_pct:>+8.2f}%{res.max_drawdown_pct:>7.2f}%"
              f"{res.n_trades:>8d}{res.win_rate:>6.1f}%{pf:>7}{res.sharpe:>8.2f}"
              f"{res.avg_trade_pnl:>+12.2f}")


if __name__ == "__main__":
    main()
