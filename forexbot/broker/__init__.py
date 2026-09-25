"""Broker adapters. Importing this package must not require the vendor SDK,
so the heavy adapter is exposed lazily through ``make_broker``."""
from .base import Broker
from .factory import make_broker

__all__ = ["Broker", "make_broker"]
