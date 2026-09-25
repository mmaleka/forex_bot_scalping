# forex_bolt_scalping

A broker-agnostic, rule-based FX trading bot. The **trading brain** (strategy +
risk) is fully separated from the **hands** (execution), so the *same* strategy
and risk code runs in backtest, paper, and live.

Execution target: **Tickerall** REST API → **Exness MT5** (demo/trial account).

## Layout

```
config/default.yaml     non-secret, tunable settings (strategy, risk, timeframe)
.env.example            copy to .env and fill in your (rotated!) secrets
forexbot/
  models.py             broker-agnostic value objects (Bar, Signal, OrderRequest, …)
  config.py             pydantic settings: YAML + .env, validated
  broker/               the ONLY vendor-specific code (Tickerall adapter + Paper sim)
  data/                 live bar feed + numpy indicators
  strategy/             pluggable strategies: ma_cross, donchian_breakout,
                        rsi_reversion, bollinger_reversion + ensemble (vote)
  risk/                 the gatekeeper: sizing + veto (daily loss, max positions)
  engine/               the live/paper event loop (orchestrator)
  store/                SQLite trade log + equity curve
  backtest/             replays history through the same strategy+risk, cost-aware
```

## Setup

```powershell
cd forex_bolt_scalping
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env        # then edit .env with your ROTATED key + password
```

> **Rotate credentials first.** If you pasted a real API key or password into
> chat, regenerate the Tickerall key and change the Exness password before
> putting them in `.env`. `.env` is git-ignored and never read by anyone but
> the process that loads it.

## Commands

```powershell
# 1) Connectivity smoke test (read-only — no orders):
forexbot check

# 2) Pull real history from the trial account:
forexbot fetch-history --bars 500 --timeframe M5 --out data/history.csv

# 3) Backtest offline (strategy + risk + costs):
forexbot backtest --bars data/history.csv --symbol EURUSDm --timeframe M5

# 4) Make synthetic bars if you just want to prove the pipeline:
python scripts/make_sample_data.py 1000 data/sample.csv
forexbot backtest --bars data/sample.csv

# 5) Run live on the trial account (mode: paper in config):
forexbot run
```

## Strategies

Four independent strategies + an **ensemble** that votes among them
(default in `config/default.yaml`):

| name                  | family          | entry trigger                                              |
|-----------------------|-----------------|------------------------------------------------------------|
| `ma_cross`            | trend           | EMA fast/slow cross                                        |
| `donchian_breakout`   | trend           | close breaks prior N-bar high/low (Turtle-style)           |
| `rsi_reversion`       | mean-reversion  | RSI flushes an extreme, then turns back through the level  |
| `bollinger_reversion` | mean-reversion  | close pierces a band, then re-enters inside it             |

The **ensemble** is itself just a `Strategy`: every bar each member votes
`OPEN_LONG`/`OPEN_SHORT`/`CLOSE`/`HOLD` exactly as if standalone, and the
ensemble opens only when a side's *weighted* votes reach `min_votes` (default 2
of 4) and beat the opposing side. Solo false signals die in the vote; a setup
two independent families agree on gets taken. Reversals work the same way — a
strong opposite vote closes and flips via the engine, and every open carries
the members' ATR stop-loss.

```powershell
# head-to-head on the same bars (standalone vs ensemble):
python scripts/compare_strategies.py --bars data/sample.csv
```

To trade one strategy alone, set `strategy.name` to its registry name in
`config/default.yaml` (e.g. `rsi_reversion`) — the risk gate, engine, and
backtester all work unchanged, since every strategy implements the same
`on_bar → Signal` contract.

## Safety built in

- **Paper-first** — `mode: paper` targets the Exness trial account; `live` is a
  flag you flip only after backtest + a real paper period look honest.
- **Idempotent orders** — every order carries an `idempotency_key`, so a retry
  can never double-fill.
- **Risk gate** — the strategy's signal is a wish; the risk manager sizes it
  (0.5% equity/trade) and can veto (daily-loss halt, max positions).
- **Broker-owned SL/TP** — stops live on the broker; the engine reconciles
  stop-outs so the trade log stays complete.
- **Trailing take-profit** — once a trade is `activation_points` in profit the
  stop ratchets `trail_points` behind the best price (toward the price only,
  re-placed in `step_points` increments). In backtest it's simulated with the
  same math (bar extremes, conservative fill order); live it's applied as
  broker-side stop modifications, so the lock-in survives a bot restart.
