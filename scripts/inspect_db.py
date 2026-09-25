"""Quick performance inspection of the live/paper trade database."""
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "bot.sqlite3"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

def ts(row):
    return row["ts"]

# ---- trades ----
trades = cur.execute("SELECT * FROM trades ORDER BY ts").fetchall()
print(f"=== {len(trades)} trade rows ===")
# Show all trades
def fmt(v, w=8):
    return " " * w if v is None else format(v, f"<{w}")

for t in trades:
    pnl = t["pnl"]
    pnl_s = f"{pnl:+.2f}" if pnl is not None else "   open"
    print(f"  {t['ts']}  {t['action']:<13} {fmt(t['side'],5)} {fmt(t['symbol'],9)} "
          f"vol={fmt(t['volume'],7)} px={fmt(t['price'],10)} {pnl_s:>8}  {t['reason']}")

# ---- closed-trade stats ----
closed = [t for t in trades if t["pnl"] is not None]
wins = [t for t in closed if t["pnl"] > 0]
losses = [t for t in closed if t["pnl"] <= 0]
gross_win = sum(t["pnl"] for t in wins)
gross_loss = sum(t["pnl"] for t in losses)
print(f"\n=== closed-trade stats ===")
print(f"closed: {len(closed)}   wins: {len(wins)}   losses: {len(losses)}")
if closed:
    print(f"win rate: {100*len(wins)/len(closed):.1f}%")
print(f"gross win:  {gross_win:+.2f}")
print(f"gross loss: {gross_loss:+.2f}")
print(f"net:        {gross_win+gross_loss:+.2f}")
if wins:
    print(f"avg win:    {gross_win/len(wins):+.2f}")
if losses:
    print(f"avg loss:   {gross_loss/len(losses):+.2f}")
if wins and losses:
    print(f"profit factor: {gross_win/abs(gross_loss):.2f}")
    print(f"payoff (avg win / avg loss): {abs(gross_win/len(wins) / (gross_loss/len(losses))):.2f}")

# ---- per symbol ----
print(f"\n=== per-symbol (closed) ===")
by_sym = defaultdict(lambda: [0, 0.0, 0, 0.0])
for t in closed:
    d = by_sym[t["symbol"]]
    d[0] += 1
    d[1] += t["pnl"]
    if t["pnl"] > 0: d[2] += 1
    else: d[3] += 1
for s, (n, pnl, w, l) in sorted(by_sym.items(), key=lambda kv: kv[1][1]):
    print(f"  {s:<10} n={n:<3} net={pnl:+8.2f}  w={w} l={l}")

# ---- by reason ----
print(f"\n=== by close reason (closed) ===")
by_reason = defaultdict(lambda: [0, 0.0])
for t in closed:
    d = by_reason[t["reason"]]
    d[0] += 1
    d[1] += t["pnl"]
for r, (n, pnl) in sorted(by_reason.items(), key=lambda kv: kv[1][1]):
    print(f"  {r:<24} n={n:<3} net={pnl:+9.2f}")

# ---- equity ----
eq = cur.execute("SELECT * FROM equity ORDER BY ts").fetchall()
print(f"\n=== equity curve ({len(eq)} points) ===")
if eq:
    first, last = eq[0], eq[-1]
    print(f"first: {first['ts']}  equity={first['equity']:.2f} bal={first['balance']:.2f}")
    print(f"last:  {last['ts']}  equity={last['equity']:.2f} bal={last['balance']:.2f}")
    print(f"change: {last['equity']-first['equity']:+.2f}")
    peak = max(e["equity"] for e in eq)
    print(f"peak equity: {peak:.2f}   max drawdown from peak: "
          f"{min(e['equity']-peak for e in eq):+.2f}")
