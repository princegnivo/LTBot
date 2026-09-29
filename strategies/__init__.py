"""Registre des stratégies. Les fichiers strategy_*.py sont ceux de l'utilisateur, inchangés."""
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from . import strategy_1m, strategy_2m, strategy_5s
from .signal import Signal


@dataclass(frozen=True)
class StrategyInfo:
    key: str
    label: str
    timeframe: int          # secondes par bougie analysée (bougies normales fournies)
    expiration: int         # secondes
    default_mg_steps: int   # étapes de martingale par défaut (après la 1re mise)
    analyze: Callable[[pd.DataFrame, str], Optional[Signal]]
    min_candles: int


REGISTRY = {
    "1M": StrategyInfo("1M", "1M · SMA5 + Williams %R (HA 60s)", 60, 60, 2, strategy_1m.analyze, 40),
    "2M": StrategyInfo("2M", "2M · Bollinger + MACD (HA 120s)", 120, 120, 0, strategy_2m.analyze, 40),
    "5s": StrategyInfo("5s", "5s · RSI14 + mèche (5s)", 5, 5, 2, strategy_5s.analyze, 40),
}

__all__ = ["REGISTRY", "StrategyInfo", "Signal"]
