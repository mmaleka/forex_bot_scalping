"""Reconcile local trade-store events vs broker fills for the 09-24 15:00 window."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "bot.sqlite3"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

CUTOFF = "2026-09-24T15:00"  # UTC; ISO-8601 same-format strings compare correctly

def show(action, cutoff, label=None):
    rows = db.execute(
        "SELECT ts, symbol, side, volume, ticket, pnl, reason FROM trades "
        "WHERE action=? AND ts >= ? ORDER BY ts", (action, cutoff)
    ).fetchall()
    print(f"--- {label or action} (>= {cutoff}): {len(rows)} ---")
    for r in rows:
        print(f"  {r['ts']} {str(r['symbol']):<8} {str(r['side']):<4} {str(r['volume']):<6} "
              f"tk={r['ticket']} pnl={r['pnl']} {(r['reason'] or '')[:75]}")

print("==== WINDOW: 2026-09-24 15:00 UTC -> now ====")
for a in ("open", "open_failed", "vetoed", "closed_by_broker", "trail"):
    show(a, CUTOFF)

print("\n==== CONTEXT: 2026-09-24 00:00-15:00 UTC (before window) ====")
for a in ("open", "open_failed", "vetoed"):
    rows = db.execute(
        "SELECT ts, symbol, side, volume, ticket, pnl, reason FROM trades "
        "WHERE action=? AND ts >= ? AND ts < ? ORDER BY ts", (a, "2026-09-24T00:00", CUTOFF)
    ).fetchall()
    print(f"--- {a}: {len(rows)} ---")
    for r in rows:
        print(f"  {r['ts']} {str(r['symbol']):<8} {str(r['side']):<4} {str(r['volume']):<6} "
              f"tk={r['ticket']} pnl={r['pnl']} {(r['reason'] or '')[:75]}")
db.close()
