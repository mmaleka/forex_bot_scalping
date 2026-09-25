"""Dump equity curve anomalies."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "bot.sqlite3"
con = sqlite3.connect(DB)
rows = con.execute("SELECT ts, equity, balance, open_positions FROM equity ORDER BY id").fetchall()
print(f"{len(rows)} rows")
if rows:
    print("first 3:", rows[:3])
    print("last 3:", rows[-3:])
    # find the weird values
    vals = [r[1] for r in rows if r[1] is not None]
    print(f"min={min(vals)} max={max(vals)}")
    # show rows with open_positions > 0 or equity jumps
    for i in range(1, len(rows)):
        prev, cur = rows[i-1], rows[i]
        if cur[1] is None or prev[1] is None:
            continue
        if abs(cur[1] - prev[1]) > 50:
            print(f"JUMP {prev[0]} {prev[1]:.2f} -> {cur[0]} {cur[1]:.2f}  (opos {prev[3]} -> {cur[3]})")
    # show the min region
    mn = min(r[1] for r in rows if r[1] is not None)
    hits = [r for r in rows if r[1] is not None and r[1] <= mn + 5]
    print("min-region rows:", hits[:10])
    # show the max region
    mx = max(r[1] for r in rows if r[1] is not None)
    hits = [r for r in rows if r[1] is not None and r[1] >= mx - 5]
    print("max-region rows:", hits[:10])
