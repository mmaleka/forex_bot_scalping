"""Check whether duplicated closed_by_broker rows share a ticket."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "bot.sqlite3"
con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT ts, symbol, side, volume, ticket, pnl FROM trades "
    "WHERE action='closed_by_broker' ORDER BY ts").fetchall()

cur = None
for ts, sym, side, vol, ticket, pnl in rows:
    key = (sym, vol)
    mark = ""
    if cur == key:
        mark = "  <== consecutive same symbol+vol"
    print(f"{ts} {sym:<9} {side:<4} {vol:<5} ticket={ticket} pnl={pnl}{mark}")
    cur = key
