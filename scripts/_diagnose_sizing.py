"""Why the risk manager vetoes small sizes, and what risk_per_trade_pct fixes it.

For every M15 bar, compute the ensemble's ATR×2 stop distance, then the
risk-based lot size at the CURRENT settings (equity 436.17, 0.5% risk) and
count how many fall below the broker's 0.01 minimum. Then report the
risk_per_trade_pct that WOULD clear the minimum on 50/75/90% of bars.

This is the primary (USD-quoted, rate=1.0) case — EURUSD. JPY/cross pairs
differ by the FX rate; see the note at the bottom.

Usage:
    python scripts/_diagnose_sizing.py data/yf_history.csv
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diagnose_votes_m15 import resample_5m_to_15m  # noqa: E402
from forexbot.backtest.runner import load_bars_csv  # noqa: E402
from forexbot.data import indicators as ta  # noqa: E402

EQUITY = 436.17
CONTRACT = 100_000.0
MIN_LOT = 0.01
ATR_PERIOD = 14
ATR_MULT = 2.0  # ensemble stop = entry ± ATR_MULT × ATR


def volume_for(pct: float, distance: float, equity: float = EQUITY, rate: float = 1.0) -> float:
    risk_amount = equity * pct / 100.0
    pnl_per_lot = distance * CONTRACT * rate
    return risk_amount / pnl_per_lot if pnl_per_lot > 0 else 0.0


def pct_to_clear(distance: float, equity: float = EQUITY, rate: float = 1.0, lot: float = MIN_LOT) -> float:
    """risk_per_trade_pct needed so the size lands exactly at ``lot``."""
    return lot * distance * CONTRACT * rate * 100.0 / equity


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/yf_history.csv"
    bars = resample_5m_to_15m(load_bars_csv(path))
    close = np.array([b.close for b in bars], float)
    high = np.array([b.high for b in bars], float)
    low = np.array([b.low for b in bars], float)
    n = len(bars)
    warm = 40

    atr = ta.atr(high, low, close, ATR_PERIOD)
    dist = np.array([ATR_MULT * a for a in atr])  # ensemble stop distance

    pcts = []
    veto_at_05 = 0
    for i in range(warm, n):
        if np.isnan(dist[i]) or dist[i] <= 0:
            continue
        pcts.append(pct_to_clear(dist[i]))
        if volume_for(0.5, dist[i]) < MIN_LOT:
            veto_at_05 += 1

    pcts = np.array(pcts)
    m = len(pcts)
    print(f"bars analyzed: {m}  (equity={EQUITY}, contract={CONTRACT}, min_lot={MIN_LOT}, ATR×{ATR_MULT})\n")
    print("stop distance (ATR×2) distribution:")
    for pctl in (10, 25, 50, 75, 90):
        d = np.percentile(dist[warm:], pctl)
        print(f"  p{pctl:<2}: {d*10000:.1f} pips  ({d:.5f})")

    print(f"\nsize at CURRENT 0.5% risk  →  vetoed (below {MIN_LOT}): {veto_at_05}/{m}  ({veto_at_05/m*100:.0f}%)")

    print("\nrisk_per_trade_pct needed to CLEAR the 0.01 minimum on:")
    for frac, label in ((50, "half the bars"), (75, "75% of bars"), (90, "90% of bars"), (95, "95% of bars")):
        need = np.percentile(pcts, frac)
        print(f"  {label:<14}: {need:.2f}%  (≈ {EQUITY*need/100:.2f} risked/trade)")

    # Show a few concrete examples
    print("\nexamples (distance → size at 0.5% / size at 1.0% / size at 1.5%):")
    for i in range(warm, n, max(1, (n - warm) // 8)):
        d = dist[i]
        if np.isnan(d):
            continue
        print(f"  {bars[i].timestamp:%m-%d %H:%M}  {d*10000:5.1f} pips  →  "
              f"{volume_for(0.5, d)*100:5.3f} / {volume_for(1.0, d)*100:5.3f} / {volume_for(1.5, d)*100:5.3f}  (0.5/1.0/1.5%)")

    print("\nNOTE: EURUSD shown (quote=USD, rate=1.0). JPY pairs divide the")
    print("needed % by ~USDJPY, crosses by their FX rate — so the required")
    print("risk_per_trade_pct differs per symbol. A single global % is a")
    print("compromise; per-symbol sizing or a min-lot floor handles it exactly.")


if __name__ == "__main__":
    main()
