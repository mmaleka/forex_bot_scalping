"""One-off: show failed open attempts and their reasons from the trade store."""
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "data" / "bot.sqlite3"
con = sqlite3.connect(db)
cur = con.cursor()
print("== failed / timeout rows (newest 25) ==")
q = ("select ts, action, symbol, side, volume, price, ticket, pnl, reason "
     "from trades where action like '%fail%' or reason like '%timeout%' "
     "order by id desc limit 25")
for r in cur.execute(q):
    print(r)

print("\n== distinct action values with counts ==")
for r in cur.execute("select action, count(*) from trades group by action order by 2 desc"):
    print(r)
