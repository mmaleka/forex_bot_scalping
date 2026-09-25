"""Verify the fixed stream API path: connect, subscribe, receive a real tick.

Read-only: keeps the session warm (same call the running bot makes), opens a
stream, listens for ticks on EURUSDm, closes the stream. No orders, no
sessions.end — the live bot's session is not disturbed.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from tickerall import Tickerall

env = {k: os.getenv(k, "") for k in
       ("TICKERALL_API_KEY", "EXNESS_SERVER", "EXNESS_ACCOUNT", "EXNESS_PASSWORD")}
client = Tickerall(api_key=env["TICKERALL_API_KEY"])

print("== StreamNamespace (factory) ==", type(client.stream).__name__)
print("   factory has .connect:", hasattr(client.stream, "connect"))
print("   factory has .on    :", hasattr(client.stream, "on"))

session = client.sessions.keep_alive(
    broker="mt5", server=env["EXNESS_SERVER"],
    account=int(env["EXNESS_ACCOUNT"]), password=env["EXNESS_PASSWORD"])
aid = str(session.account_id)
print("account:", aid)

t0 = time.monotonic()
st = client.stream.connect(timeout=20)
print(f"== connected in {time.monotonic()-t0:.1f}s ==", type(st).__name__)
print("   state:", st.get_state(), "| connected:", st.is_connected())

got = None
st.on("tick", lambda e: None)  # registration path check
st.subscribe_ticks(aid, ["EURUSDm", "GBPUSDm"])
deadline = time.monotonic() + 20
while time.monotonic() < deadline and got is None:
    for sym in ("EURUSDm", "GBPUSDm"):
        t = st.latest_tick(sym)
        if t is not None and t.bid > 0:
            got = (sym, t)
            break
    if got is None:
        time.sleep(0.3)

if got is not None:
    sym, t = got
    print(f"== LIVE TICK == {sym} bid={t.bid} ask={t.ask} ts={t.timestamp}")
else:
    print("!! no tick within 20s (market closed or subscription rejected) —")
    print("   stream itself connected fine; check rejected channels on server.")

st.close()
print("stream closed. OK.")
