"""Measure how the live ChochStrategy actually behaves on the bot's M15 bars.

Runs the EXACT class the ensemble instantiates (same params as config/default.yaml:
pivot_k=2, ATR 14, mult 2.0) bar-by-bar with the same 200-bar window the
orchestrator passes, on 5m history resampled to M15 — so the numbers describe
what the live bot will see, not a backtester's idealized setup.

Reports:
  * what fraction of bars it votes on (event frequency, per symbol)
  * long / short split of its votes
  * median gap between events (bars, and ~hours at M15)
  * how often its vote coincides with at least one other ensemble member on
    the same bar — the only case where it can actually change the tally

Usage:
    python scripts/_diagnose_choch.py data/yf_history.csv
"""
from __future__ import annotations

import os
import sys
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
from _diagnose_votes import ma_cross, donchian, rsi_reversion, bollinger  # noqa: E402
from _diagnose_votes_m15 import resample_5m_to_15m  # noqa: E402
from forexbot.backtest.runner import load_bars_csv  # noqa: E402
from forexbot.models import Action  # noqa: E402
from forexbot.strategy.choch_strategy import ChochStrategy  # noqa: E402


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/yf_history.csv"
    bars = resample_5m_to_15m(load_bars_csv(path))
    n = len(bars)
    warm = 60
    print(f"{bars[0].symbol}  {n} M15 bars  "
          f"({bars[0].timestamp:%Y-%m-%d} → {bars[-1].timestamp:%Y-%m-%d})\n")

    # Exact live config: config/default.yaml → choch_strategy params {pivot_k: 2}
    choch = ChochStrategy({"pivot_k": 2})
    window = 200  # orchestrator: self.feeds[sym].window(200)

    # Other members' per-bar votes (diagnose-script replicas, same defaults)
    close = np.array([b.close for b in bars], float)
    high = np.array([b.high for b in bars], float)
    low = np.array([b.low for b in bars], float)
    other_long = (np.asarray(ma_cross(close)[0], int)
                  + np.asarray(donchian(high, low, close)[0], int)
                  + np.asarray(rsi_reversion(close)[0], int)
                  + np.asarray(bollinger(close)[0], int))
    other_short = (np.asarray(ma_cross(close)[1], int)
                   + np.asarray(donchian(high, low, close)[1], int)
                   + np.asarray(rsi_reversion(close)[1], int)
                   + np.asarray(bollinger(close)[1], int))

    events = []          # (idx, action)
    reasons = []
    for i in range(warm, n):
        win = bars[max(0, i - window + 1):i + 1]
        sig = choch.on_bar(win, None)
        if sig.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
            events.append((i, sig.action))
            if len(reasons) < 5:
                reasons.append(f"  bar {i} ({bars[i].timestamp:%m-%d %H:%M}) {sig.reason}")

    traded = n - warm
    longs = sum(1 for _, a in events if a == Action.OPEN_LONG)
    shorts = len(events) - longs
    pct = len(events) / traded * 100
    print(f"CHoCH votes: {len(events)} of {traded} bars  ({pct:.3f}%)   "
          f"long {longs} / short {shorts}")

    if len(events) >= 2:
        gaps = np.diff([i for i, _ in events])
        print(f"gap between events: median {int(np.median(gaps))} M15 bars "
              f"(~{np.median(gaps)*15/60:.1f} h), max {int(gaps.max())} bars "
              f"(~{gaps.max()*15/60:.1f} h)")
    print("sample events:")
    print("\n".join(reasons) or "  (none)")

    # Coincidence with other members: the ONLY bars where CHoCH can change the
    # tally (min_votes=2, so a lone CHoCH vote never trades by itself).
    coin_l = sum(1 for i, a in events if a == Action.OPEN_LONG and other_long[i] >= 1)
    coin_s = sum(1 for i, a in events if a == Action.OPEN_SHORT and other_short[i] >= 1)
    coin = coin_l + coin_s
    if events:
        print(f"\ncoinceded with ≥1 other member: {coin}/{len(events)} "
              f"({coin/len(events)*100:.1f}%)   long {coin_l} / short {coin_s}")
        days = (bars[-1].timestamp - bars[0].timestamp).total_seconds() / 86400
        print(f"→ ~{coin/days:.2f} decision-relevant CHoCH votes per day on this symbol")
        # ...and how many of those are the *decisive* 2nd vote (tally 1→2,
        # with no opposing votes on the bar)
        tipped = sum(1 for i, a in events
                     if (a == Action.OPEN_LONG and other_long[i] == 1 and other_short[i] == 0)
                     or (a == Action.OPEN_SHORT and other_short[i] == 1 and other_long[i] == 0))
        print(f"of those, bars where CHoCH is the *decisive* 2nd vote (1→2, no opposing): {tipped}")

    # Strict-inequality sensitivity: this feed quotes sparse one-sided levels,
    # so check how often adjacent M15 highs are EQUAL (a swing needs strict >).
    eq = int((np.diff(high) == 0).sum()) + int((np.diff(low) == 0).sum())
    print(f"\nM15 bars with flat high OR low vs previous bar: "
          f"{eq/ (traded-1) *100:.1f}% (strict-inequality swings are skipped on ties)")


if __name__ == "__main__":
    main()
