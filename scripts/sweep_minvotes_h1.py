"""Sweep ensemble vote thresholds on H1, all configured symbols.

Tests three ensemble regimes on the SAME cached H1 bars + same costs as
scripts/backtest_all_h1.py (spread 2 pts, slippage 1 pt, $7/lot, 0.5% risk,
ATR×2 stop, trailing 30/20/5):

  baseline   min_votes=2, min_close_votes=2   (current live config)
  any-vote   min_votes=1, min_close_votes=1   (one member fires → trade)
  fast-in    min_votes=1, min_close_votes=2   (enter on one, exit needs two)

Goal: find the regime with the most trades that still keeps an edge
(return / PF / maxDD) — the "missing opportunities" question, measured.

Usage:
    python scripts/sweep_minvotes_h1.py                    # all symbols
    python scripts/sweep_minvotes_h1.py --symbols EURUSDm GBPJPYm
"""
from __future__ import annotations

import argparse
import copy
import csv
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run without pip install

from forexbot.backtest import Backtester, load_bars_csv
from forexbot.config import load_settings
from forexbot.utils import parse_pair

VARIANTS = [
    ("baseline mv2/mcv2", {"min_votes": 2, "min_close_votes": 2}),
    ("any-vote mv1/mcv1", {"min_votes": 1, "min_close_votes": 1}),
    ("fast-in  mv1/mcv2", {"min_votes": 1, "min_close_votes": 2}),
]


def usdjpy_rate(data_dir: Path) -> float:
    """USD value of 1 JPY from cached USDJPYm H1 bars (JPY-cross honesty)."""
    bars = load_bars_csv(str(data_dir / "USDJPYm.h1.csv"), "USDJPYm")
    return 1.0 / (sum(b.close for b in bars) / len(bars))


def run_variant(name: str, overrides: dict, settings, symbols, data_dir, fx_rates,
                args) -> list:
    s = copy.deepcopy(settings)
    s.timeframe = "H1"
    s.strategy.params = {**s.strategy.params, **overrides}
    rows = []
    for k, sym in enumerate(symbols, 1):
        t0 = time.monotonic()
        csv_path = data_dir / f"{sym}.h1.csv"
        if not csv_path.exists():
            rows.append({"symbol": sym, "error": "no cached H1 data"})
            continue
        bars = load_bars_csv(str(csv_path), sym)
        s.symbols = [sym]
        try:
            bt = Backtester(s, start_equity=args.equity, commission_per_lot=args.commission,
                            slippage_points=args.slippage, spread_points=args.spread,
                            fx_rates=fx_rates)
            res = bt.run(bars)
            pf = res.profit_factor
            days = max(1, (bars[-1].timestamp - bars[0].timestamp).total_seconds() / 86400)
            rows.append({
                "symbol": sym, "bars": len(bars),
                "start_equity": res.start_equity, "end_equity": round(res.end_equity, 2),
                "net_pnl": round(res.net_pnl, 2), "return_pct": round(res.return_pct, 2),
                "max_dd_pct": round(res.max_drawdown_pct, 2),
                "trades": res.n_trades, "wins": res.wins, "losses": res.losses,
                "win_rate_pct": round(res.win_rate, 1),
                "profit_factor": round(pf, 2) if math.isfinite(pf) else "inf",
                "avg_trade_pnl": round(res.avg_trade_pnl, 2),
                "sharpe": round(res.sharpe, 2),
                "trades_per_month": round(res.n_trades / (days / 30.4), 2),
            })
            print(f"  [{k:2d}/{len(symbols)}] {sym:<9} → ret {res.return_pct:+7.2f}%  "
                  f"maxDD {res.max_drawdown_pct:5.2f}%  trades {res.n_trades:4d}  "
                  f"win {res.win_rate:5.1f}%  PF {pf if pf == 'inf' else round(pf, 2)}   "
                  f"({time.monotonic() - t0:.1f}s)")
        except Exception as e:  # one bad pair must not kill the sweep
            print(f"  !! {sym}: {type(e).__name__}: {e}")
            rows.append({"symbol": sym, "error": f"{type(e).__name__}: {e}"})
    return rows


def summarize(label: str, rows: list) -> dict:
    ok = [r for r in rows if "error" not in r]
    tot_net = sum(r["net_pnl"] for r in ok)
    tot_tr = sum(r["trades"] for r in ok)
    tot_win = sum(r["wins"] for r in ok)
    pos_syms = sum(1 for r in ok if r["return_pct"] > 0)
    # aggregate profit factor from gross sums
    gross_win = sum(r["avg_trade_pnl"] * r["wins"] for r in ok if r["avg_trade_pnl"] > 0)
    gross_loss = -sum(r["avg_trade_pnl"] * r["losses"] for r in ok if r["avg_trade_pnl"] < 0)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    avg_trades_per_month = (tot_tr / len(ok)) / (730 / 30.4) if ok else 0.0
    return {"label": label, "symbols": len(ok), "positive": pos_syms,
            "trades": tot_tr, "avg_trades_per_month": round(avg_trades_per_month, 1),
            "net_pnl": round(tot_net, 2), "win_rate": round(100 * tot_win / tot_tr, 1) if tot_tr else 0,
            "pf": round(pf, 2) if math.isfinite(pf) else "inf",
            "worst_maxDD": max(r["max_dd_pct"] for r in ok) if ok else 0,
            "avg_return_pct": round(sum(r["return_pct"] for r in ok) / len(ok), 2) if ok else 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--equity", type=float, default=10000.0)
    p.add_argument("--spread", type=int, default=2)
    p.add_argument("--slippage", type=int, default=1)
    p.add_argument("--commission", type=float, default=7.0)
    p.add_argument("--data-dir", default="data/h1")
    args = p.parse_args()

    settings = load_settings(None)
    symbols = args.symbols or settings.symbols
    data_dir = Path(args.data_dir)
    fx_rates = {"JPY": usdjpy_rate(data_dir)} if any(parse_pair(s)[1] == "JPY" for s in symbols) else None
    print(f"symbols: {len(symbols)}   fx JPY→USD: {fx_rates}   equity {args.equity:,.0f}/symbol")

    all_rows = {}
    for name, overrides in VARIANTS:
        print(f"\n=== {name}  (min_votes={overrides['min_votes']}, min_close_votes={overrides['min_close_votes']}) ===")
        all_rows[name] = run_variant(name, overrides, settings, symbols, data_dir, fx_rates, args)

    # ── per-variant per-symbol tables ───────────────────────────────────────
    for name, rows in all_rows.items():
        ok = [r for r in rows if "error" not in r]
        ok.sort(key=lambda r: r["return_pct"], reverse=True)
        hdr = (f"{'symbol':<10}{'return':>9}{'maxDD':>8}{'trades':>8}{'win%':>7}"
               f"{'PF':>7}{'t/mo':>7}{'avg trade':>11}")
        print(f"\n── {name} ──")
        print(hdr)
        for r in ok:
            pf = r["profit_factor"]
            print(f"{r['symbol']:<10}{r['return_pct']:>+8.2f}%{r['max_dd_pct']:>7.2f}%"
                  f"{r['trades']:>8d}{r['win_rate_pct']:>6.1f}%{str(pf):>7}"
                  f"{r['trades_per_month']:>7.1f}{r['avg_trade_pnl']:>+11.2f}")
        for r in rows:
            if "error" in r:
                print(f"{r['symbol']:<10}   ERROR {r['error']}")

    # ── head-to-head summary ────────────────────────────────────────────────
    print("\n════════ VARIANT HEAD-TO-HEAD (per-symbol sum, fresh equity each) ════════")
    summaries = [summarize(n, r) for n, r in all_rows.items()]
    hdr = (f"{'variant':<20}{'symbols':>8}{'pos syms':>9}{'trades':>8}{'tr/sym/mo':>11}"
           f"{'net $':>10}{'win%':>7}{'PF':>7}{'worst DD':>9}{'avg ret%':>9}")
    print(hdr)
    print("─" * len(hdr))
    for s_ in summaries:
        print(f"{s_['label']:<20}{s_['symbols']:>8d}{s_['positive']:>9d}{s_['trades']:>8d}"
              f"{s_['avg_trades_per_month']:>11.1f}{s_['net_pnl']:>+10.2f}"
              f"{s_['win_rate']:>6.1f}%{str(s_['pf']):>7}{s_['worst_maxDD']:>8.2f}%"
              f"{s_['avg_return_pct']:>+8.2f}%")

    # ── save ────────────────────────────────────────────────────────────────
    out = data_dir / "sweep_minvotes_results.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["variant", "symbol", "return_pct", "max_dd_pct", "trades",
                    "win_rate_pct", "profit_factor", "avg_trade_pnl", "trades_per_month"])
        for name, rows in all_rows.items():
            for r in rows:
                if "error" in r:
                    w.writerow([name, r["symbol"], "ERROR", r["error"], "", "", "", "", ""])
                else:
                    w.writerow([name, r["symbol"], r["return_pct"], r["max_dd_pct"], r["trades"],
                                r["win_rate_pct"], r["profit_factor"], r["avg_trade_pnl"],
                                r["trades_per_month"]])
    print(f"\nresults → {out}")


if __name__ == "__main__":
    main()
