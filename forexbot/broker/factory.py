"""Adapter factory — lazily imports the vendor SDK only when we actually need it.

This keeps ``import forexbot`` (and the backtester, which only ever uses
``PaperBroker``) working even on a machine where ``tickerall`` isn't installed.
"""
from __future__ import annotations

from ..config import Settings
from .base import Broker


def make_broker(settings: Settings) -> Broker:
    name = settings.broker.name
    if name == "paper":
        from .paper import PaperBroker
        return PaperBroker(settings)
    if name == "tickerall":
        from .tickerall import TickerallBroker
        return TickerallBroker(settings)
    raise ValueError(f"Unknown broker adapter: {name!r} (expected 'tickerall' or 'paper')")
