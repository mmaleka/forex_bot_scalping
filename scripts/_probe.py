"""READ-ONLY probe of the live Tickerall/Exness account. No orders are placed.

Prints the real response shapes so the broker adapter can be aligned to them.
"""
import os
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
    print("== session ==", type(session).__name__)
    print("  account_id :", session.account_id)
    print("  is_demo    :", session.is_demo, "| status:", session.status)
    aid = session.account_id

    detail = client.accounts.get(aid)
    print("\n== accounts.get ==", type(detail).__name__)
    print("  status     :", detail.status, "| online:", detail.online)
    print("  hint       :", detail.hint)
    acc = detail.account
    if acc is not None:
        print("  ACCOUNT    :", type(acc).__name__)
        for f in ("balance", "equity", "currency", "leverage", "free_margin", "margin"):
            print(f"    {f:<10}:", getattr(acc, f, None))
    else:
        print("  account    : None (offline/cold?)")
    print("  positions  :", len(detail.positions))

    syms = client.accounts.symbols(aid)
    want = ["EURUSDm","GBPUSDm","USDJPYm","AUDUSDm","USDCADm","USDCHFm","NZDUSDm","EURGBPm","EURJPYm","GBPJPYm"]
    print("\n== accounts.symbols ==")
    print("  total      :", len(syms))
    print("  our 10 ok  :", {w: (w in syms) for w in want})

    specs = client.accounts.symbol_specs(aid)
    print("\n== accounts.symbol_specs ==", len(specs), "specs")
    for s in specs[:6]:
        print(f"  {s.name:<10} min={s.volume_min!r} max={s.volume_max!r} step={s.volume_step!r} "
              f"src={s.spec_source} digits={s.digits} point={s.point!r} contract={s.contract_size!r} "
              f"tick_value={s.tick_value!r}")
    eur = next((s for s in specs if s.name == "EURUSDm"), None)
    if eur is not None:
        print("  EURUSDm    : min", eur.volume_min, "max", eur.volume_max, "step", eur.volume_step,
              "src", eur.spec_source, "contract", eur.contract_size)

    print("\n== candles.get (EURUSDm, last 3 M5) ==")
    try:
        bars = client.candles.get(aid, symbol="EURUSDm", count=3, timeframe="M5")
        for b in bars:
            print(f"  ts={b.timestamp} o={b.open} h={b.high} l={b.low} c={b.close} spread={b.spread}")
        if not bars:
            print("  (no bars returned)")
    except Exception as e:
        print("  candles error:", type(e).__name__, e)
finally:
    try:
        client.sessions.end(aid)
    except Exception as e:
        print("end-session note:", e)
print("\nprobe complete (read-only; session ended).")
