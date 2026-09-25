"""Generate a synthetic OHLC random-walk CSV for offline pipeline testing.

NOT real market data — just enough shape (trend + noise) to prove that
strategy → risk → paper-fill → reporting all run end to end. For a real backtest,
pull actual history with `forexbot fetch-history --bars 500 --out data/history.csv`.

Usage:  python scripts/make_sample_data.py [num_bars] [out_csv]
"""
import csv
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main(bars: int = 1000, out: str = "data/sample.csv", start: float = 1.0850):
    random.seed(7)
    ts = datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc)
    price = start
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "symbol", "open", "high", "low", "close"])
        for _ in range(bars):
            o = price
            c = max(0.5, o + random.gauss(0.0, 0.0004))
            hi = max(o, c) + random.uniform(0, 0.0003)
            lo = min(o, c) - random.uniform(0, 0.0003)
            w.writerow([ts.isoformat(), "EURUSDm",
                        f"{o:.5f}", f"{hi:.5f}", f"{lo:.5f}", f"{c:.5f}"])
            price = c
            ts += timedelta(minutes=5)
    print(f"wrote {bars} synthetic bars → {out}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    outp = sys.argv[2] if len(sys.argv) > 2 else "data/sample.csv"
    main(n, outp)
