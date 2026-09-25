"""Command-line entry point:  ``forexbot <command>``

  run            Run the live/paper loop (mode from config).
  backtest       Replay a CSV of bars through strategy + risk (offline).
  fetch-history  Pull bar history from the broker and save to CSV.
  fetch-yf       Pull FX bar history from Yahoo Finance (yfinance) and save to CSV.
  check          Connectivity smoke test: connect, print account + symbol specs (read-only).
  report         Show recent trades / equity from the local store.
"""
from __future__ import annotations

import argparse

from .config import load_settings


def _common(p):
    p.add_argument("--config", default=None,
                   help="path to a YAML config (default: config/default.yaml)")


def cmd_run(args):
    s = load_settings(args.config)
    from .engine import Orchestrator
    print(f"mode={s.mode}  symbols={len(s.symbols)} ({', '.join(s.symbols)})  "
          f"tf={s.timeframe}  strategy={s.strategy.name}")
    print(f"risk: {s.risk.risk_per_trade_pct}%/trade · max {s.risk.max_concurrent_positions} "
          f"position(s) total, {s.risk.max_positions_per_symbol} per pair · "
          f"daily-loss cap {s.risk.max_daily_loss_pct}%")
    print("(Ctrl+C to stop cleanly)")
    Orchestrator(s).run()


def cmd_backtest(args):
    s = load_settings(args.config)
    if args.symbol:
        s.symbols = [args.symbol]
    if args.timeframe:
        s.timeframe = args.timeframe
    from .backtest import Backtester, load_bars_csv
    bars = load_bars_csv(args.bars, s.primary_symbol)
    print(f"loaded {len(bars)} bars for {s.primary_symbol}  "
          f"({bars[0].timestamp:%Y-%m-%d} → {bars[-1].timestamp:%Y-%m-%d})")
    bt = Backtester(s, start_equity=args.equity,
                    spread_points=args.spread, slippage_points=args.slippage)
    res = bt.run(bars)
    print("\n── backtest ──")
    print(res.summary())


def cmd_fetch_history(args):
    s = load_settings(args.config)
    import csv
    from .broker import make_broker
    b = make_broker(s)
    b.connect()
    bars = b.candles(s.primary_symbol, args.timeframe or s.timeframe, args.bars)
    from pathlib import Path
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "symbol", "open", "high", "low", "close"])
        for bar in bars:
            w.writerow([bar.timestamp.isoformat(), bar.symbol,
                        bar.open, bar.high, bar.low, bar.close])
    b.disconnect()
    print(f"wrote {len(bars)} bars → {args.out}")


def cmd_fetch_yf(args):
    """Pull FX history from Yahoo Finance — no broker account or API key needed.

    Writes the exact CSV shape ``load_bars_csv`` reads, labeled with the bot's
    traded symbol so the backtest paper-broker lookup matches. Timestamps are
    converted to UTC; the still-forming final bar is dropped.
    """
    import csv
    import re
    from datetime import datetime, timezone
    from pathlib import Path

    import yfinance as yf

    s = load_settings(args.config)
    symbol = args.symbol or s.primary_symbol
    t = yf.Ticker(args.yf_symbol)
    try:
        df = t.history(interval=args.interval, period=args.period, auto_adjust=False)
    except Exception as e:
        raise SystemExit(f"yfinance request failed: {e}")
    if df is None or df.empty:
        raise SystemExit(
            f"yfinance returned no data for {args.yf_symbol} @ {args.interval}/{args.period}.\n"
            "Note: minute bars (1m/5m/15m/30m) only go back ~60 days — try "
            "--period 60d, or a coarser --interval (60m goes back ~2y).")

    # Drop the still-forming bar: its open time is less than one interval ago.
    m = re.match(r"(\d+)([mhd])", args.interval)
    if m:
        secs = int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
        last = df.index[-1]
        age = (datetime.now(timezone.utc) - last.to_pydatetime().astimezone(timezone.utc)).total_seconds()
        if 0 <= age < secs:
            df = df.iloc[:-1]

    # FX rows with NaN/zero extremes are garbage bars — drop them.
    before = len(df)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df["High"] > 0) & (df["Low"] > 0) & (df["Open"] > 0) & (df["Close"] > 0)]
    dropped = before - len(df)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    idx = df.index.tz_convert("UTC") if df.index.tz is not None else df.index
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for ts, row in df.iterrows():
            w.writerow([ts.isoformat(), symbol,
                        f"{float(row['Open']):.5f}", f"{float(row['High']):.5f}",
                        f"{float(row['Low']):.5f}", f"{float(row['Close']):.5f}",
                        int(row.get("Volume") or 0)])

    print(f"wrote {len(df)} bars → {out}   ({args.yf_symbol}, {args.interval}, {args.period})")
    if dropped:
        print(f"dropped {dropped} incomplete/garbage bars")
    print(f"range: {idx[0]} → {idx[-1]}")
    print("next:  forexbot backtest --bars " + args.out)


def cmd_check(args):
    s = load_settings(args.config)
    from .broker import make_broker
    b = make_broker(s)
    acc = b.connect()
    print(f"account   {acc.account_id}")
    print(f"equity    {acc.equity:,.2f} {acc.currency}")
    print(f"balance   {acc.balance:,.2f} {acc.currency}")
    print(f"open pos  {acc.open_position_count}")
    for sym in s.symbols:
        spec = b.symbol_specs(sym)
        print(f"symbol    {spec.symbol}  step={spec.volume_step}  min={spec.volume_min}  max={spec.volume_max}")
    b.disconnect()
    print("OK — connected; read-only smoke test passed.")


def cmd_report(args):
    from .store import Store
    store = Store(args.db)
    print("── recent trades ──")
    rows = store.trades(args.limit)
    if not rows:
        print("(no trades logged yet)")
    for t in rows:
        print(f"{t['ts']}  {t['action']:<14} {t['symbol']} {t['side']} {t['volume']}  "
              f"pnl={t['pnl']}  {t['reason'] or ''}")
    eq = store.equity_curve()
    if eq:
        print(f"\n── equity curve: {len(eq)} points · latest {eq[-1]['equity']:,.2f} ──")


def build_parser():
    p = argparse.ArgumentParser(prog="forexbot", description="Rule-based FX scalping bot.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the live/paper loop"); _common(r); r.set_defaults(fn=cmd_run)

    b = sub.add_parser("backtest", help="replay a CSV of bars offline"); _common(b)
    b.add_argument("--bars", required=True, help="CSV: timestamp,open,high,low,close")
    b.add_argument("--symbol"); b.add_argument("--timeframe")
    b.add_argument("--equity", type=float, default=10000.0)
    b.add_argument("--spread", type=int, default=2, help="typical spread, in points")
    b.add_argument("--slippage", type=int, default=1, help="slippage, in points")
    b.set_defaults(fn=cmd_backtest)

    f = sub.add_parser("fetch-history", help="pull bar history from the broker → CSV"); _common(f)
    f.add_argument("--bars", type=int, default=500)
    f.add_argument("--timeframe")
    f.add_argument("--out", default="data/sample.csv")
    f.set_defaults(fn=cmd_fetch_history)

    y = sub.add_parser("fetch-yf", help="pull FX history from Yahoo Finance (yfinance) → CSV")
    _common(y)
    y.add_argument("--yf-symbol", default="EURUSD=X", help="Yahoo ticker (default EURUSD=X)")
    y.add_argument("--symbol", default=None, help="symbol label for the CSV (default: the bot's symbol)")
    y.add_argument("--interval", default="5m", help="yfinance interval: 1m 5m 15m 30m 60m 1h 1d")
    y.add_argument("--period", default="60d", help="yfinance period (minute bars top out at 60d)")
    y.add_argument("--out", default="data/yf_history.csv")
    y.set_defaults(fn=cmd_fetch_yf)

    c = sub.add_parser("check", help="connectivity smoke test (read-only)"); _common(c)
    c.set_defaults(fn=cmd_check)

    rt = sub.add_parser("report", help="show trades/equity from the local store")
    rt.add_argument("--db", default="data/bot.sqlite3")
    rt.add_argument("--limit", type=int, default=20)
    rt.set_defaults(fn=cmd_report)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
