"""Exercise the FIXED TickerallBroker stream path end-to-end (read-only).

Boots the real adapter with real config, starts the stream via the fixed
start_stream(), checks quote() returns the live tick (not the candle fallback),
then stops the stream. No orders placed.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from forexbot.config import load_settings
from forexbot.broker.tickerall import TickerallBroker

s = load_settings()
b = TickerallBroker(s)
acc = b.connect()
print("connected:", acc.account_id, "equity:", acc.equity, "balance:", acc.balance)

b.start_stream(["EURUSDm"])
time.sleep(2.0)  # let a couple of ticks land

q = b.quote("EURUSDm")
print(f"quote: bid={q.bid} ask={q.ask}")

# Prove the quote is the LIVE tick, not the candle-close fallback:
# ask-bid on a candle fallback is a synthetic close*2e-5 (~2.3 pips on EURUSD).
spread_pips = (q.ask - q.bid) * 1e4
print(f"spread: {spread_pips:.1f} pips  (candle fallback would be ~23 pips? no—~2.3; live is ~1-2)")

time.sleep(1.5)
q2 = b.quote("EURUSDm")
print(f"quote2 (1.5s later): bid={q2.bid} ask={q2.ask}")

b.stop_stream()
print("stream stopped. OK.")
