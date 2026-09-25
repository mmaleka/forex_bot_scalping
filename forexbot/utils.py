"""Small shared helpers (time, timeframe parsing). Kept dependency-free."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple


def parse_pair(symbol: str) -> Tuple[str, str]:
    """Best-effort (base, quote) from a pair name: 'EURUSDm' → ('EUR', 'USD').

    Tolerates broker suffixes (the trailing 'm' on Exness micros). Anything
    that doesn't look like a 6-letter pair falls back to EUR/USD.
    """
    letters = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(letters) >= 6:
        return letters[:3], letters[3:6]
    return "EUR", "USD"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def tf_minutes(tf: str) -> int:
    """Parse a timeframe string (M1/M5/H1/D1) into minutes."""
    tf = tf.strip().upper()
    if tf.endswith("M"):
        return max(1, int(tf[:-1] or 1))
    if tf.endswith("H"):
        return int(tf[:-1] or 1) * 60
    if tf.endswith("D"):
        return int(tf[:-1] or 1) * 1440
    if tf.endswith("W"):
        return int(tf[:-1] or 1) * 10080
    return 5


def tf_delta(tf: str) -> timedelta:
    return timedelta(minutes=tf_minutes(tf))
