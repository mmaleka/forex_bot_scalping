"""Performance analysis of bot trades 2026-09-23/24/25 (DB is UTC)."""
import sqlite3
from pathlib import Path
from collections import Counter

DB = Path(__file__).resolve().parents[1] / "data" / "bot.sqlite3"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== 09-23 trades ===")
for r in cur.execute("SELECT * FROM trades WHERE ts LIKE '2026-09-23%' ORDER BY ts"):
    print(r["ts"], r["action"], r["symbol"], r["side"], r["volume"], r["price"], r["pnl"], (r["reason"] or "")[:70])

print("\n=== 09-25 trades ===")
for r in cur.execute("SELECT * FROM trades WHERE ts LIKE '2026-09-25%' ORDER BY ts"):
    print(r["ts"], r["action"], r["symbol"], r["side"], r["volume"], r["price"], r["pnl"], (r["reason"] or "")[:70])

print("\n=== 09-24 per-action counts ===")
for r in cur.execute("SELECT action, COUNT(*) n FROM trades WHERE ts LIKE '2026-09-24%' GROUP BY action ORDER BY n DESC"):
    print(r["action"], r["n"])

print("\n=== 09-24 realized P&L (closed_by_broker rows) ===")
rows = cur.execute(
    "SELECT ts, symbol, side, volume, pnl, reason FROM trades "
    "WHERE ts LIKE '2026-09-24%' AND action='closed_by_broker' ORDER BY ts"
).fetchall()
tot = 0.0
for r in rows:
    tot += r["pnl"] or 0.0
    print(r["ts"], r["symbol"], r["side"], r["volume"], round(r["pnl"] or 0, 4))
print("SUM of all closed_by_broker pnl rows:", round(tot, 4))

print("\n=== equity: min/max 09-24 and context ===")
for r in cur.execute(
    "SELECT ts, equity, balance, open_positions FROM equity "
    "WHERE ts LIKE '2026-09-24%' AND equity < 200 ORDER BY ts"
):
    print(r["ts"], r["equity"], r["balance"], r["open_positions"])
for r in cur.execute(
    "SELECT ts, equity, balance, open_positions FROM equity "
    "WHERE ts LIKE '2026-09-24%' AND open_positions >= 4 ORDER BY ts"
):
    print(r["ts"], r["equity"], r["balance"], r["open_positions"])
