"""Same vote diagnosis as _diagnose_votes.py, but on the bot's ACTUAL timeframe.

Resamples the 5m history to 15m (3 bars -> 1, OHLC-aggregated) and replays the
identical per-member trigger logic + ensemble vote rule, so the consensus rate
matches what the live M15 engine sees.

Usage:
    python scripts/_diagnose_votes_m15.py data/yf_history.csv
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diagnose_votes import ma_cross, donchian, rsi_reversion, bollinger  # noqa: E402
from forexbot.backtest.runner import load_bars_csv  # noqa: E402


def resample_5m_to_15m(bars):
    """Group 5m bars into 15m OHLC bars (open=first, high=max, low=min, close=last)."""
    from forexbot.models import Bar
    out = []
    bucket_start = None
    o = h = l = c = None
    sym = bars[0].symbol
    for b in bars:
        aligned = b.timestamp.replace(minute=(b.timestamp.minute // 15) * 15, second=0, microsecond=0)
        if bucket_start is None:
            bucket_start, o, h, l, c = aligned, b.open, b.high, b.low, b.close
            continue
        if aligned != bucket_start:
            out.append(Bar(symbol=sym, timestamp=bucket_start, open=o, high=h, low=l, close=c))
            bucket_start, o, h, l, c = aligned, b.open, b.high, b.low, b.close
        else:
            h = max(h, b.high); l = min(l, b.low); c = b.close
    if bucket_start is not None:
        out.append(Bar(symbol=sym, timestamp=bucket_start, open=o, high=h, low=l, close=c))
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/yf_history.csv"
    bars_5m = load_bars_csv(path)
    bars = resample_5m_to_15m(bars_5m)
    close = np.array([b.close for b in bars], float)
    high = np.array([b.high for b in bars], float)
    low = np.array([b.low for b in bars], float)
    n = len(bars)
    warm = 60
    print(f"resampled 5m → 15m: {len(bars_5m)} → {n} bars  "
          f"({bars[0].timestamp:%Y-%m-%d} → {bars[-1].timestamp:%Y-%m-%d})  {bars[0].symbol}\n")

    ma_l, ma_s = ma_cross(close)
    do_l, do_s = donchian(high, low, close)
    rs_l, rs_s = rsi_reversion(close)
    bo_l, bo_s = bollinger(close)
    members = [("ma_cross", ma_l, ma_s), ("donchian", do_l, do_s),
               ("rsi_rev", rs_l, rs_s), ("boll_rev", bo_l, bo_s)]

    print("member fire counts (long / short / total) on M15:")
    for name, l, s in members:
        print(f"  {name:<10} {int(l[warm:].sum()):>5} / {int(s[warm:].sum()):>5} / {int((l|s)[warm:].sum()):>5}")

    votes_long = np.zeros(n, int); votes_short = np.zeros(n, int)
    for _, l, s in members:
        votes_long += l.astype(int); votes_short += s.astype(int)

    tally = Counter()
    for i in range(warm, n):
        tally[(int(votes_long[i]), int(votes_short[i]))] += 1

    print("\nbar vote distribution (long_votes, short_votes) → #bars:")
    for (lv, sv), cnt in sorted(tally.items(), key=lambda x: (-x[0][0] - x[0][1], x[0])):
        print(f"  long {lv} / short {sv} : {cnt}")

    min_votes = 2
    cons_long = sum(1 for i in range(warm, n) if votes_long[i] >= min_votes and votes_long[i] > votes_short[i])
    cons_short = sum(1 for i in range(warm, n) if votes_short[i] >= min_votes and votes_short[i] > votes_long[i])
    traded_bars = n - warm
    print(f"\nensemble consensus (≥{min_votes} votes, one side) on M15:")
    print(f"  long  : {cons_long}  ({cons_long/traded_bars*100:.2f}% of bars)")
    print(f"  short : {cons_short}  ({cons_short/traded_bars*100:.2f}% of bars)")
    days = (bars[-1].timestamp - bars[0].timestamp).total_seconds() / 86400
    print(f"  →  ~{(cons_long+cons_short)/days:.2f} consensus events per day per symbol")

    lone = sum(1 for i in range(warm, n) if (votes_long[i] + votes_short[i]) == 1)
    print(f"\nbars with exactly 1 vote: {lone}  ({lone/traded_bars*100:.2f}%)")
    none = sum(1 for i in range(warm, n) if votes_long[i] + votes_short[i] == 0)
    print(f"bars with 0 votes       : {none}  ({none/traded_bars*100:.2f}%)")


if __name__ == "__main__":
    main()
