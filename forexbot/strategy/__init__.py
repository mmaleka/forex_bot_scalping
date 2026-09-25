"""Strategy registry — add a new strategy by subclassing ``Strategy`` and
decorating it with ``@register``; it becomes selectable from config by name."""
from __future__ import annotations

from typing import Dict, Optional, Type

from .base import Strategy
from .ma_cross import MaCross
from .rsi_reversion import RsiReversion
from .bollinger_reversion import BollingerReversion
from .donchian_breakout import DonchianBreakout
from .choch_strategy import ChochStrategy
from .ensemble import Ensemble

_REGISTRY: Dict[str, Type[Strategy]] = {
    MaCross.name: MaCross,
    RsiReversion.name: RsiReversion,
    BollingerReversion.name: BollingerReversion,
    DonchianBreakout.name: DonchianBreakout,
    ChochStrategy.name: ChochStrategy,
    Ensemble.name: Ensemble,
}


def register(cls: Type[Strategy]) -> Type[Strategy]:
    _REGISTRY[cls.name] = cls
    return cls


def make_strategy(name: str, params: Optional[dict] = None) -> Strategy:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown strategy {name!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[name](params or {})


__all__ = [
    "Strategy", "MaCross", "RsiReversion", "BollingerReversion",
    "DonchianBreakout", "ChochStrategy", "Ensemble", "make_strategy", "register",
]
