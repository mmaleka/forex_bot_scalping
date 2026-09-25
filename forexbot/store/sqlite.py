"""SQLite persistence: trade log, equity curve, and a small resumable-state store.

Stdlib ``sqlite3`` only. The bot loop is the single writer, so a fresh
connection per operation is plenty (and keeps it thread-safe without extra locks).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from ..utils import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  symbol TEXT, side TEXT, volume REAL, price REAL,
  ticket TEXT, pnl REAL, reason TEXT
);
CREATE TABLE IF NOT EXISTS equity(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, equity REAL, balance REAL, open_positions INTEGER
);
CREATE TABLE IF NOT EXISTS state(
  key TEXT PRIMARY KEY, value TEXT
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.path)

    def log_trade(self, action: str, symbol=None, side=None, volume=None,
                  price=None, ticket=None, pnl=None, reason=None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trades(ts, action, symbol, side, volume, price, ticket, pnl, reason) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (utcnow().isoformat(), action, symbol, side, volume, price, ticket, pnl, reason))

    def log_equity(self, equity: float, balance: float, open_positions: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO equity(ts, equity, balance, open_positions) VALUES(?,?,?,?)",
                (utcnow().isoformat(), equity, balance, open_positions))

    def set_state(self, key: str, value):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO state(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))

    def get_state(self, key: str):
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def trades(self, limit: int = 100):
        cols = ("ts", "action", "symbol", "side", "volume", "price", "ticket", "pnl", "reason")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, action, symbol, side, volume, price, ticket, pnl, reason "
                "FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def equity_curve(self):
        cols = ("ts", "equity", "balance", "open_positions")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, equity, balance, open_positions FROM equity ORDER BY id").fetchall()
        return [dict(zip(cols, r)) for r in rows]


__all__ = ["Store"]
