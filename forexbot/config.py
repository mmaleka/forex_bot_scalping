"""Configuration: non-secret YAML + secret .env, validated by pydantic.

Split by design:
  - ``config/default.yaml``  → tunable behavior (strategy, risk, timeframe) [committed]
  - ``.env``                 → secrets (API key, broker creds)               [git-ignored]
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

# Project root = the folder that contains `.env` and `config/`.
ROOT = Path(__file__).resolve().parent.parent


class StrategyConfig(BaseModel):
    name: str = "ma_cross"
    params: Dict = Field(default_factory=dict)


class BrokerConfig(BaseModel):
    name: Literal["tickerall", "paper"] = "tickerall"
    broker: str = "mt5"  # Tickerall broker type (Exness = mt5)
    terminal_type: Literal["MOBILE", "WEB", "CLIENT"] = "MOBILE"
    keep_alive: bool = True
    # Filled from .env in load_settings():
    server: str = ""
    account: int = 0
    password: str = ""


class RiskConfig(BaseModel):
    sizing_mode: Literal["risk", "fixed"] = "risk"
    fixed_lot_size: float = 0.1      # fixed position size (lots) — non-JPY pairs ("fixed" mode)
    fixed_lot_size_jpy: float = 0.01 # fixed position size (lots) — JPY pairs ("fixed" mode)
    risk_per_trade_pct: float = 0.5  # "risk" mode: % of equity risked between entry and stop
    # Small account + broker min lot: the risk-based size can land below the
    # broker's minimum (worst on JPY pairs / wide ATR). When true, fill at the
    # min lot instead of skipping the trade — this risks slightly MORE than
    # risk_per_trade_pct on those bars (bounded to the min lot's stop distance)
    # so the bot actually fills. Set false to restore the strict "never exceed
    # the risk budget" behavior (vetoes those bars).
    floor_to_min_lot: bool = True
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 0.0  # 0 → no fixed take-profit
    max_concurrent_positions: int = 10  # account-wide open-position cap
    max_positions_per_symbol: int = 1   # cap per pair (one position per symbol)
    max_daily_loss_pct: float = 3.0
    max_exposure_pct: float = 10.0
    atr_period: int = 14
    contract_size: float = 100000.0  # standard FX lot = 100k units of base


class TrailingConfig(BaseModel):
    """Trailing take-profit: the stop follows price once it runs in our favor.

    Distances are in *points* (1 point = 1e-5 on a 5-digit FX pair).
    """
    enabled: bool = False
    activation_points: float = 20.0  # profit (from entry) before trailing starts
    trail_points: float = 15.0      # stop stays this far behind the best price
    step_points: float = 5.0        # only re-place when the stop improves by ≥ this


class EngineConfig(BaseModel):
    stream: bool = True
    poll_interval_s: float = 1.0
    reconnect_backoff_s: float = 5.0


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Optional[str] = "data/bot.log"


class Settings(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    symbols: List[str] = Field(default_factory=lambda: ["EURUSDm"])
    symbol: Optional[str] = None  # legacy single-symbol; folded into `symbols`
    timeframe: str = "M5"
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    trailing: TrailingConfig = Field(default_factory=TrailingConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # Secrets (populated from .env, never persisted to disk).
    tickerall_api_key: str = ""

    @model_validator(mode="after")
    def _resolve_symbols(self) -> "Settings":
        """Normalize the pair list. ``symbols`` is the canonical multi-pair
        field; the legacy single ``symbol`` (old configs) becomes the only pair."""
        if self.symbol:
            self.symbols = [self.symbol]
        if not self.symbols:
            self.symbols = ["EURUSDm"]
        return self

    @property
    def primary_symbol(self) -> str:
        """First symbol — what the single-symbol tools (backtest, fetch, check) use."""
        return self.symbols[0]


def _load_yaml(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def load_settings(config_path: Optional[str] = None) -> Settings:
    """Merge YAML defaults with ``.env`` secrets into a validated ``Settings``."""
    cfg_file = Path(config_path) if config_path else ROOT / "config" / "default.yaml"
    data = _load_yaml(cfg_file)

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("TICKERALL_API_KEY", "")

    broker = dict(data.get("broker") or {})
    broker["server"] = os.getenv("EXNESS_SERVER", broker.get("server", ""))
    broker["account"] = int(os.getenv("EXNESS_ACCOUNT", "0") or 0)
    broker["password"] = os.getenv("EXNESS_PASSWORD", "")
    data["broker"] = broker

    settings = Settings(**data)
    settings.tickerall_api_key = api_key
    return settings


def redact(s: "Settings") -> dict:
    """A log-safe snapshot of settings (secrets masked)."""
    d = s.model_dump()
    d["broker"]["password"] = "***" if d["broker"].get("password") else ""
    d["tickerall_api_key"] = "***" if d.get("tickerall_api_key") else ""
    return d
