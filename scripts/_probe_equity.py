"""READ-ONLY: poll accounts.get N times, print balance/equity/margin/positions.

Goal: see whether the broker's `equity` field wobbles (as the store's equity
curve showed: 0.00 when flat, -136..+611 with positions open) — broker-side
garbage vs. adapter mapping bug.
"""
import os
import time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from tickerall import Tickerall

api_key = os.getenv("TICKERALL_API_KEY", "")
server = os.getenv("EXNESS_SERVER", "")
account = os.getenv("EXNESS_ACCOUNT", "")
password = os.getenv("EXNESS_PASSWORD", "")

client = Tickerall(api_key=api_key)
try:
    session = client.sessions.start(
        broker="mt5", server=server, account=int(account), password=password)
    aid = session.account_id
    print(f"session {aid} (demo={getattr(session, 'is_demo', '?')})")
    for i in range(10):
        d = client.accounts.get(aid)
        acc = d.account
        npos = len(d.positions or [])
        print(f"poll {i:2d}: balance={acc.balance!r:14} equity={acc.equity!r:14} "
              f"free_margin={acc.free_margin!r} margin={getattr(acc, 'margin', None)!r} "
              f"positions={npos}")
        time.sleep(1.5)
finally:
    try:
        client.sessions.end(aid)
    except Exception as e:
        print("end-session note:", e)
print("probe complete (read-only; session ended).")
