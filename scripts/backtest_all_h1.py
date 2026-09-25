"""H1 backtest across ALL configured symbols.

For each symbol in config/default.yaml:
  1. Ensure ~2 years of hourly bars exist under data/h1/ (fetched from Yahoo
     Finance on first use, then cached — pass --refresh to re-download).
  2. Replay them through the SAME strategy + risk + trailing code the live
     engine uses (spread, slippage, and commission already paid).
  3. Print one comparison table, sorted by return, and save it to
     data/h1/backtest_all_h1_results.csv.

Usage:
    python scripts/backtest_all_h1.py                          # all symbols
    python scripts/backtest_all_h1.py --symbols EURUSDm USDJPYm
    python scripts/backtest_all_h1.py --refresh --equity 436   # re-fetch, account-sized
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run without pip install

from forexbot.backtest import Backtester, load_bars_csv
from forexbot.config import load_settings
from forexbot.utils import parse_pair

HOURS_SECS = 3600


# Exness 6-letter symbols that don't map 1:1 onto Yahoo tickers:
_YF_OVERRIDES = {
    "USDDKAm": "USDDKK=X",  # Exness truncates DKK → "USDDKA"; Yahoo spells it USDDKK
    "USDSKSm": "USDCZK=X",  # SKS = old ISO code for the Czech koruna; Yahoo uses CZK
}


def yf_symbol_for(symbol: str) -> str:
    """'EURUSDm' → 'EURUSD=X'  (drop the Exness 'm' suffix, add Yahoo's =X)."""
    if symbol in _YF_OVERRIDES:
        return _YF_OVERRIDES[symbol]
    pair = symbol[:-1] if symbol.endswith("m") else symbol
    return f"{pair}=X"


def fetch_h1(symbol: str, yf_symbol: str, out: Path, period: str) -> int:
    """Pull 60m bars from Yahoo and write the CSV shape load_bars_csv reads.

    Same cleaning as the fetch-yf CLI command: drop the still-forming final bar
    and any NaN/zero garbage rows.
    """
    import yfinance as yf

    df = yf.Ticker(yf_symbol).history(interval="60m", period=period, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {yf_symbol} @ 60m/{period}")

    last = df.index[-1]
    age = (datetime.now(timezone.utc) - last.to_pydatetime().astimezone(timezone.utc)).total_seconds()
    if 0 <= age < HOURS_SECS:
        df = df.iloc[:-1]

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df["High"] > 0) & (df["Low"] > 0) & (df["Open"] > 0) & (df["Close"] > 0)]

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
    print(f"  fetched {len(df)} H1 bars → {out}   ({idx[0]:%Y-%m-%d} → {idx[-1]:%Y-%m-%d})")
    return len(df)


def usdjpy_rate(data_dir: Path, period: str) -> float:
    """USD value of 1 JPY (e.g. 1/152.5), from cached USDJPYm H1 bars.

    Needed so JPY-quoted crosses (GBPJPY/CADJPY/…) are sized and marked in
    honest USD instead of at parity (~150× off). Fetches the reference pair
    if it isn't cached yet.
    """
    csv_path = data_dir / "USDJPYm.h1.csv"
    if not csv_path.exists():
        fetch_h1("USDJPYm", "USDJPY=X", csv_path, period)
    bars = load_bars_csv(str(csv_path), "USDJPYm")
    avg = sum(b.close for b in bars) / len(bars)
    return 1.0 / avg


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="*", default=None,
                   help="subset of configured symbols (default: all in config)")
    p.add_argument("--usdjpy", type=float, default=None,
                   help="USDJPY reference rate for JPY-cross P&L (default: avg of cached data)")
    p.add_argument("--equity", type=float, default=10000.0)
    p.add_argument("--spread", type=int, default=2, help="spread, in points")
    p.add_argument("--slippage", type=int, default=1, help="slippage, in points")
    p.add_argument("--commission", type=float, default=7.0, help="commission per lot")
    p.add_argument("--period", default="730d", help="yfinance history window (max for 60m)")
    p.add_argument("--refresh", action="store_true", help="re-download even if cached")
    p.add_argument("--data-dir", default="data/h1")
    args = p.parse_args()

    s = load_settings(None)
    s.timeframe = "H1"
    symbols = args.symbols or s.symbols
    data_dir = Path(args.data_dir)

    # JPY-cross honesty: give the risk manager + paper broker a real JPY→USD
    # rate so GBPJPY/CADJPY/… aren't sized and marked at parity (~150× off).
    fx_rates = None
    if args.usdjpy:
        fx_rates = {"JPY": 1.0 / args.usdjpy}
    elif any(parse_pair(sym)[1] == "JPY" for sym in symbols):
        jpy = usdjpy_rate(data_dir, args.period)
        fx_rates = {"JPY": jpy}
        print(f"JPY→USD reference rate: 1 JPY = {jpy:.6f} USD (avg USDJPY {1/jpy:.2f})")

    rows = []
    for k, sym in enumerate(symbols, 1):
        t0 = time.monotonic()
        csv_path = data_dir / f"{sym}.h1.csv"
        print(f"[{k}/{len(symbols)}] {sym}  ({yf_symbol_for(sym)})")
        try:
            if not csv_path.exists() or args.refresh:
                fetch_h1(sym, yf_symbol_for(sym), csv_path, args.period)
            bars = load_bars_csv(str(csv_path), sym)
            s.symbols = [sym]  # primary_symbol must match the data being replayed
            bt = Backtester(s, start_equity=args.equity, commission_per_lot=args.commission,
                            slippage_points=args.slippage, spread_points=args.spread,
                            fx_rates=fx_rates)
            res = bt.run(bars)
            pf = res.profit_factor
            rows.append({
                "symbol": sym, "bars": len(bars),
                "first": f"{bars[0].timestamp:%Y-%m-%d}", "last": f"{bars[-1].timestamp:%Y-%m-%d}",
                "start_equity": res.start_equity, "end_equity": res.end_equity,
                "net_pnl": round(res.net_pnl, 2), "return_pct": round(res.return_pct, 2),
                "max_dd_pct": round(res.max_drawdown_pct, 2),
                "trades": res.n_trades, "wins": res.wins, "losses": res.losses,
                "win_rate_pct": round(res.win_rate, 1),
                "profit_factor": round(pf, 2) if math.isfinite(pf) else "inf",
                "avg_trade_pnl": round(res.avg_trade_pnl, 2),
                "sharpe": round(res.sharpe, 2), "trail_adjustments": res.trail_adjustments,
            })
            print(f"  → return {res.return_pct:+7.2f}%  maxDD {res.max_drawdown_pct:6.2f}%  "
                  f"trades {res.n_trades:5d}  win {res.win_rate:5.1f}%  "
                  f"PF {pf:5.2f}  sharpe {res.sharpe:5.2f}   "
                  f"({time.monotonic() - t0:.1f}s)")
        except Exception as e:  # one bad pair must not kill the whole sweep
            print(f"  !! {type(e).__name__}: {e}")
            rows.append({"symbol": sym, "error": f"{type(e).__name__}: {e}"})

    if not rows:
        raise SystemExit("no results")

    ok = [r for r in rows if "error" not in r]
    ok.sort(key=lambda r: r["return_pct"], reverse=True)

    header = (f"{'symbol':<10}{'bars':>7}{'range':>25}{'return':>9}{'maxDD':>8}"
              f"{'trades':>8}{'win%':>7}{'PF':>7}{'sharpe':>8}{'avg trade':>12}")
    print("\n── H1 backtest, all symbols (sorted by return) ──")
    print(header)
    print("─" * len(header))
    for r in ok:
        pf = f"{r['profit_factor']}" if r["profit_factor"] == "inf" else f"{r['profit_factor']:.2f}"
        print(f"{r['symbol']:<10}{r['bars']:>7d}{r['first'] + '→' + r['last']:>25}"
              f"{r['return_pct']:>+8.2f}%{r['max_dd_pct']:>7.2f}%"
              f"{r['trades']:>8d}{r['win_rate_pct']:>6.1f}%{pf:>7}"
              f"{r['sharpe']:>8.2f}{r['avg_trade_pnl']:>+12.2f}")
    for r in rows:
        if "error" in r:
            print(f"{r['symbol']:<10}{'—':>7}{'—':>25}   ERROR {r['error']}")

    if ok:
        tot_net = sum(r["net_pnl"] for r in ok)
        tot_tr = sum(r["trades"] for r in ok)
        print(f"\nper-symbol sum (each ran with fresh {args.equity:,.0f} equity): "
              f"net {tot_net:+,.2f}  trades {tot_tr}")

    out_csv = data_dir / "backtest_all_h1_results.csv"
    data_dir.mkdir(parents=True, exist_ok=True)
    if ok:
        with out_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(ok[0].keys()))
            w.writeheader()
            for r in rows:
                if "error" in r:
                    w.writerow({**dict.fromkeys(ok[0].keys(), ""), "symbol": r["symbol"]})
                else:
                    w.writerow(r)
        print(f"results → {out_csv}")


if __name__ == "__main__":
    main()
