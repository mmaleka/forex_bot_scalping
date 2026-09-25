"""One-off: print all store rows for a given ticket (usage: _show_ticket.py 3275298500)."""
import sqlite3
import sys
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "data" / "bot.sqlite3"
ticket = sys.argv[1] if len(sys.argv) > 1 else "3275298500"
con = sqlite3.connect(db)
cur = con.cursor()
tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
print("tables:", tables)
for t in tables:
    cols = [c[1] for c in cur.execute(f"pragma table_info({t})")]
    if "ticket" not in cols:
        continue
    print(f"\n== {t} ({', '.join(cols)}) ==")
    for row in cur.execute(f"select * from {t} where ticket = ? order by rowid", (ticket,)):
        print(row)
