"""Diagnose WHY the ensemble places no trades.

Replays the exact per-member trigger logic (copied verbatim from the four
strategy classes) over a CSV of real bars, and counts — per bar — how many
members vote long / short. This isolates the ensemble from the broker, risk,
and feed, so it answers one question: do the members ever agree?

Usage:
    python scripts/_diagnose_votes.py data/yf_history.csv
"""
from __future__ import annotations

import sys
from collections import Counter

import numpy as np

from forexbot.backtest.runner import load_bars_csv
from forexbot.data import indicators as ta

# ---- Member trigger logic, copied from the strategy classes -----------------
# ma_cross: EMA20/EMA50 cross event
def ma_cross(close, fast=20, slow=50):
    f = ta.ema(close, fast); s = ta.ema(close, slow)
    n = len(close)
    long = np.zeros(n, bool); short = np.zeros(n, bool)
    for i in range(1, n):
        if np.isnan(f[i]) or np.isnan(f[i-1]) or np.isnan(s[i]) or np.isnan(s[i-1]):
            continue
        prev = f[i-1] > s[i-1]; cur = f[i] > s[i]
        if not prev and cur: long[i] = True
        if prev and not cur: short[i] = True
    return long, short

# donchian_breakout: close breaks prior N-bar extreme
def donchian(high, low, close, lookback=20):
    n = len(close)
    long = np.zeros(n, bool); short = np.zeros(n, bool)
    for i in range(lookback + 1, n):
        hh = high[i-lookback:i].max(); ll = low[i-lookback:i].min()
        if close[i] > hh: long[i] = True
        if close[i] < ll: short[i] = True
    return long, short

# rsi_reversion: RSI turns back through an extreme level
def rsi_reversion(close, period=14, os_=30.0, ob_=70.0):
    r = ta.rsi(close, period)
    n = len(close)
    long = np.zeros(n, bool); short = np.zeros(n, bool)
    for i in range(1, n):
        if np.isnan(r[i]) or np.isnan(r[i-1]):
            continue
        if r[i-1] < os_ <= r[i]: long[i] = True
        if r[i-1] > ob_ >= r[i]: short[i] = True
    return long, short

# bollinger_reversion: close pierces a band, then re-enters
def bollinger(close, period=20, k=2.0):
    mid = ta.sma(close, period); sd = ta.rolling_std(close, period)
    n = len(close)
    long = np.zeros(n, bool); short = np.zeros(n, bool)
    for i in range(1, n):
        if np.isnan(mid[i]) or np.isnan(sd[i]) or np.isnan(mid[i-1]) or np.isnan(sd[i-1]):
            continue
        lo_prev = mid[i-1] - k*sd[i-1]; lo_now = mid[i] - k*sd[i]
        hi_prev = mid[i-1] + k*sd[i-1]; hi_now = mid[i] + k*sd[i]
        if close[i-1] < lo_prev and close[i] >= lo_now: long[i] = True
        if close[i-1] > hi_prev and close[i] <= hi_now: short[i] = True
    return long, short


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/yf_history.csv"
    bars = load_bars_csv(path)
    close = np.array([b.close for b in bars], float)
    high = np.array([b.high for b in bars], float)
    low = np.array([b.low for b in bars], float)
    n = len(bars)
    warm = 60  # enough for EMA50 + ATR to be valid
    print(f"bars: {n}  ({bars[0].timestamp:%Y-%m-%d} → {bars[-1].timestamp:%Y-%m-%d})  symbol={bars[0].symbol}\n")

    ma_l, ma_s = ma_cross(close)
    do_l, do_s = donchian(high, low, close)
    rs_l, rs_s = rsi_reversion(close)
    bo_l, bo_s = bollinger(close)

    members = [("ma_cross", ma_l, ma_s), ("donchian", do_l, do_s),
               ("rsi_rev", rs_l, rs_s), ("boll_rev", bo_l, bo_s)]

    # Per-member fire counts (past warm-up)
    print("member fire counts (open_long / open_short / total):")
    for name, l, s in members:
        print(f"  {name:<10} {int(l[warm:].sum()):>5} / {int(s[warm:].sum()):>5} / {int((l|s)[warm:].sum()):>5}")

    # Per-bar vote tally
    votes_long = np.zeros(n, int); votes_short = np.zeros(n, int)
    for _, l, s in members:
        votes_long += l.astype(int); votes_short += s.astype(int)

    tally = Counter()
    for i in range(warm, n):
        tally[(int(votes_long[i]), int(votes_short[i]))] += 1

    print("\nbar vote distribution (long_votes, short_votes) → #bars:")
    for (lv, sv), cnt in sorted(tally.items(), key=lambda x: (-x[0][0] - x[0][1], x[0])):
        print(f"  long {lv} / short {sv} : {cnt}")

    # Consensus = a side reaches >=2 and beats the other (the ensemble's rule)
    min_votes = 2
    cons_long = sum(1 for i in range(warm, n) if votes_long[i] >= min_votes and votes_long[i] > votes_short[i])
    cons_short = sum(1 for i in range(warm, n) if votes_short[i] >= min_votes and votes_short[i] > votes_long[i])
    traded_bars = n - warm
    print(f"\nensemble consensus (≥{min_votes} votes, one side):")
    print(f"  long  : {cons_long}  ({cons_long/traded_bars*100:.2f}% of bars)")
    print(f"  short : {cons_short}  ({cons_short/traded_bars*100:.2f}% of bars)")

    # Show a few actual consensus events so we can see they exist at all
    examples = []
    for i in range(warm, n):
        if (votes_long[i] >= min_votes and votes_long[i] > votes_short[i]) or \
           (votes_short[i] >= min_votes and votes_short[i] > votes_long[i]):
            side = "LONG" if votes_long[i] > votes_short[i] else "SHORT"
            who = [name for name, l, s in members if (side == "LONG" and l[i]) or (side == "SHORT" and s[i])]
            examples.append((bars[i].timestamp, side, int(votes_long[i]), int(votes_short[i]), who))
    print(f"\n{len(examples)} consensus event(s) found. First 10:")
    for ts, side, lv, sv, who in examples[:10]:
        print(f"  {ts:%Y-%m-%d %H:%M}  {side:<5}  long {lv}/short {sv}  {who}")

    # Also: how often does exactly ONE member fire (a lone vote, like the live log shows)?
    lone = sum(1 for i in range(warm, n) if (votes_long[i] + votes_short[i]) == 1)
    print(f"\nbars with exactly 1 vote (a lone signal, e.g. 'long 1.0'): {lone}  ({lone/traded_bars*100:.2f}%)")


if __name__ == "__main__":
    main()
