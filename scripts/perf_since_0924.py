"""Exness account performance since 2026-09-24 15:00 (broker time).

Reads the account state + closed round-trip history straight from the broker
via the Tickerall adapter, and cross-checks the local trade/equity store.
Read-only: no orders, no session teardown beyond disconnect.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from forexbot.config import load_settings
from forexbot.broker.tickerall import TickerallBroker

ROOT = Path(__file__).resolve().parents[1]
CUTOFF_BROKER = "2026-09-24T15:00"  # broker-local wall time (user's phrase)


def parse_t(s: str):
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def fmt_pnl(t) -> float:
    return (t.profit or 0.0) + t.swap + t.commission


def main() -> None:
    settings = load_settings()
    broker = TickerallBroker(settings)
    state = broker.connect()

    print("=== ACCOUNT (Exness, as reported by broker) ===")
    print(f"account={state.account_id}  currency={state.currency}")
    print(f"balance={state.balance:,.2f}  equity={state.equity:,.2f}  free_margin={state.margin_free:,.2f}")
    print(f"open positions: {len(state.positions)}")
    for p in state.positions:
        print(f"  {p.ticket}  {p.symbol:<8} {p.side:<4} {p.volume:<6} @ {p.open_price:<10} "
              f"PL={p.profit:+.2f}  SL={p.stop_loss}  TP={p.take_profit}")

    hist = broker._client.history.get(broker.account_id, limit=500)
    print(f"\n=== CLOSED HISTORY from broker: {len(hist)} round-trips (limit 500) ===")
    for t in hist[:3]:
        print(f"  sample: open={t.open_time} close={t.close_time} ticket={t.ticket}")

    # ── determine broker tz offset by cross-referencing local DB (UTC) ──────
    db = sqlite3.connect(ROOT / "data" / "bot.sqlite3")
    db.row_factory = sqlite3.Row
    local = {r["ticket"]: r["ts"] for r in db.execute("SELECT ticket, ts FROM trades")}
    tz_note = ""
    for t in hist:
        lt = local.get(t.ticket)
        if lt:
            bt = parse_t(t.open_time)
            if bt and bt.tzinfo is not None:
                tz_note = f" (broker offset {bt.utcoffset()})"
            elif bt:
                off = (datetime.fromisoformat(lt) - bt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                # naive broker time vs UTC local time → offset = local_utc - broker
                tz_note = f" (naive broker time; if broker=UTC+X then X≈{-off:.1f}h)"
            break

    # ── filter to the window (broker wall time) ──────────────────────────────
    def in_window(t) -> bool:
        bt = parse_t(t.open_time)
        if bt is None:
            return False
        if bt.tzinfo is not None:
            return bt >= datetime.fromisoformat(CUTOFF_BROKER.replace("T", "T") + "+00:00")
        return bt.strftime("%Y-%m-%dT%H:%M") >= CUTOFF_BROKER

    win = [t for t in hist if in_window(t)]
    print(f"\n=== TRADERS' WINDOW: open >= {CUTOFF_BROKER} (broker time) → {len(win)} closed trades{tz_note} ===")
    if not win:
        print("  (no closed trades in window — see open positions above)")
        broker.disconnect()
        return

    tot = wins = losses = be = 0.0
    for t in win:
        n = fmt_pnl(t)
        tot += n
        if n > 0: wins += n
        elif n < 0: losses += n
    pf = (wins / abs(losses)) if losses else float("inf")
    n_win = sum(1 for t in win if fmt_pnl(t) > 0)
    n_loss = sum(1 for t in win if fmt_pnl(t) < 0)
    dur = []
    for t in win:
        o, c = parse_t(t.open_time), parse_t(t.close_time)
        if o and c and o.tzinfo == c.tzinfo:
            dur.append((c - o).total_seconds())

    print(f"trades: {len(win)}   wins: {n_win}   losses: {n_loss}   BE: {len(win)-n_win-n_loss}")
    print(f"win rate: {100*n_win/len(win):.0f}%")
    print(f"total P&L (profit+swap+commission): {tot:+,.2f} {state.currency}")
    print(f"profit factor: {pf:.2f}   gross win: {wins:,.2f}   gross loss: {losses:,.2f}")
    if dur:
        print(f"duration: min {min(dur)/60:.1f}m  max {max(dur)/3600:.1f}h  avg {sum(dur)/len(dur)/60:.0f}m")
    by_sym = defaultdict(lambda: [0, 0.0])
    for t in win:
        by_sym[t.symbol][0] += 1
        by_sym[t.symbol][1] += fmt_pnl(t)
    print("\nper symbol:")
    for sym, (n, pnl) in sorted(by_sym.items(), key=lambda kv: -kv[1][1]):
        print(f"  {sym:<8} n={n:<3} {pnl:+.2f}")

    print("\ntrade list (broker time):")
    for t in win:
        o, c = parse_t(t.open_time), parse_t(t.close_time)
        d = f"{(c-o).total_seconds()/3600:.1f}h" if (o and c and o.tzinfo == c.tzinfo) else "?"
        print(f"  {t.open_time} → {t.close_time:<20} {t.symbol:<8} {t.side:<4} "
              f"{t.volume:<6} {t.open_price} → {t.close_price:<10} {fmt_pnl(t):+.2f}  [{d}] "
              f"sw={t.swap:+.1f} com={t.commission:+.1f}")

    # ── local DB cross-check + equity curve ──────────────────────────────────
    print("\n=== LOCAL DB cross-check (UTC) ===")
    rows = db.execute(
        "SELECT action, COUNT(*) n FROM trades WHERE ts >= '2026-09-24 13:00' GROUP BY action ORDER BY n DESC"
    ).fetchall()
    print("action counts since 2026-09-24 13:00 UTC (=15:00 SAST):")
    for r in rows:
        print(f"  {r['n']:<4} {r['action']}")
    eq = db.execute(
        "SELECT ts, equity, balance, open_positions FROM equity WHERE ts >= '2026-09-24 13:00' ORDER BY ts"
    ).fetchall()
    if eq:
        bal0, eqs = eq[0]["equity"], [r["equity"] for r in eq]
        peak, mdd = eqs[0], 0.0
        for v in eqs:
            peak = max(peak, v)
            mdd = max(mdd, peak - v)
        print(f"equity snapshots: {len(eq)}   start {eq[0]['ts']} {eqs[0]:.2f} → last {eq[-1]['ts']} {eqs[-1]:.2f}")
        print(f"max drawdown in window: {mdd:.2f}   low {min(eqs):.2f} / high {max(eqs):.2f}")
        print(f"balance now per local store: {eq[-1]['balance']:.2f}")
    db.close()

    # ── liveness ─────────────────────────────────────────────────────────────
    logp = ROOT / "data" / "bot.log"
    if logp.exists():
        lines = logp.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"\n=== bot.log tail (last {min(6, len(lines))} lines) ===")
        for ln in lines[-6:]:
            print("  " + ln[:160])

    broker.disconnect()


if __name__ == "__main__":
    main()
