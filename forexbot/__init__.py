"""forexbot — a broker-agnostic, rule-based FX bot.

Core idea: the *trading brain* (strategy + risk) is fully separated from the
*hands* (execution). Everything above the ``Broker`` interface is identical
across backtest, paper, and live — so the code you validate on history is the
exact code that trades.
"""

__version__ = "0.1.0"
