"""Resample an OHLCV CSV to a coarser timeframe (bucket by N minutes, OHLC aggregate).

Writes the exact shape load_bars_csv reads. Timestamps must share one tz offset
(no DST flip mid-file) — the bucketing is string-based on the minute field.

Usage:
    python scripts/_resample.py <src.csv> <dst.csv> <bucket_minutes>
    python scripts/_resample.py data/yf_history.csv data/yf_m15.csv 15
    python scripts/_resample.py data/yf_history.csv data/yf_h1_11w.csv 60
"""
from __future__ import annotations

import csv
import sys


def resample(src: str, dst: str, bucket: int):
    rows = []
    with open(src, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    rows.sort(key=lambda r: r["timestamp"])

    out = []
    bucket_key = None
    o = h = l = c = None
    sym = rows[0]["symbol"]
    for r in rows:
        ts = r["timestamp"]
        minute = int(ts[14:16])
        key = ts[:14] + f"{(minute // bucket) * bucket:02d}" + ts[16:]
        if bucket_key is None:
            bucket_key, o, h, l, c = key, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            continue
        if key != bucket_key:
            out.append((bucket_key, sym, o, h, l, c))
            bucket_key, o, h, l, c = key, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        else:
            h = max(h, float(r["high"]))
            l = min(l, float(r["low"]))
            c = float(r["close"])
    if bucket_key is not None:
        out.append((bucket_key, sym, o, h, l, c))

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for ts, s, o, h, l, c in out:
            w.writerow([ts, s, f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{c:.5f}", 0])
    print(f"wrote {len(out)} bars (bucket {bucket}m) from {len(rows)} rows → {dst}")


if __name__ == "__main__":
    resample(
        sys.argv[1] if len(sys.argv) > 1 else "data/yf_history.csv",
        sys.argv[2] if len(sys.argv) > 2 else "data/yf_resampled.csv",
        int(sys.argv[3]) if len(sys.argv) > 3 else 15,
    )
