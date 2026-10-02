"""Registre des stratégies."""
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from . import strategy_1m, strategy_2m, strategy_5m, strategy_5s
from .signal import Signal


@dataclass(frozen=True)
class StrategyInfo:
    key: str
    label: str
    timeframe: int          # secondes par bougie analysée (bougies normales fournies par le courtier)
    expiration: int         # secondes
    analyze: Callable[[pd.DataFrame, str], Optional[Signal]]
    min_candles: int
    max_delay: int          # manuel : délai (s) pendant lequel le bouton « Placer le trade » reste valable
    ha: bool = False        # bougies Heikin Ashi (converties dans la stratégie)
    martingale: bool = True


REGISTRY = {
    "5s": StrategyInfo("5s", "5s · RSI14 + mèche / Bollinger 13", 5, 5, strategy_5s.analyze, 40, 10),
    "1M": StrategyInfo("1M", "1M · Bollinger + SMA2/5 + RSI8 (HA)", 60, 60, strategy_1m.analyze, 40, 30, ha=True),
    "2M": StrategyInfo("2M", "2M · Bollinger 6 + MACD 6/19/6", 120, 120, strategy_2m.analyze, 45, 60),
    "5M": StrategyInfo("5M", "5M · SMA3 × SMA50 (tendance)", 60, 300, strategy_5m.analyze, 70, 60),
}

__all__ = ["REGISTRY", "StrategyInfo", "Signal"]
