"""
strategy_5m.py
--------------
Stratégie "5M" — marchés en TENDANCE. Deux moyennes mobiles : SMA 3 (rapide) et SMA 50 (lente).
Bougies normales d'1 minute, expiration 5 minutes.

    ACHAT (CALL) : la SMA 3 croise la SMA 50 à la hausse
    VENTE (PUT)  : la SMA 3 croise la SMA 50 à la baisse
Filtres de la vidéo :
    - éviter les marchés en range : beaucoup de croisements des deux moyennes sur les dernières bougies
    - éviter les grosses bougies : bougie (actuelle ou précédente) nettement plus grande que la moyenne
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import indicators as ind
from strategies.signal import Signal


@dataclass
class Strategy5mConfig:
    expiration: int = 300
    ma_fast_period: int = 3
    ma_slow_period: int = 50
    cross_lookback: int = 2          # croisement sur la bougie actuelle (0) ou la précédente (1)
    range_window: int = 20           # fenêtre de détection d'un marché en range
    max_crosses: int = 2             # > 2 croisements dans la fenêtre = range -> on s'abstient
    big_candle_mult: float = 2.5     # bougie > 2,5 x la taille moyenne = « grosse bougie »
    big_candle_window: int = 20
    min_candles: int = 70


def _crosses(fast: pd.Series, slow: pd.Series, i: int, window: int) -> int:
    diff = (fast - slow).iloc[max(0, i - window): i + 1].dropna()
    signs = diff[diff != 0].apply(lambda v: 1 if v > 0 else -1)
    return int((signs != signs.shift()).sum() - 1) if len(signs) else 0


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy5mConfig] = None) -> Optional[Signal]:
    """df : bougies normales de 60 s (time, open, high, low, close)."""
    cfg = cfg or Strategy5mConfig()
    if len(df) < cfg.min_candles:
        return None

    close = df["close"].astype(float)
    fast, slow = ind.sma(close, cfg.ma_fast_period), ind.sma(close, cfg.ma_slow_period)
    i = len(df) - 1
    if pd.isna(slow.iloc[i]):
        return None

    up_age = ind.cross_age(fast, slow, i, cfg.cross_lookback, "up")
    dn_age = ind.cross_age(fast, slow, i, cfg.cross_lookback, "down")
    call_ok = up_age is not None and fast.iloc[i] > slow.iloc[i]      # le croisement tient toujours
    put_ok = dn_age is not None and fast.iloc[i] < slow.iloc[i]
    if not call_ok and not put_ok:
        return None

    # ---- filtre « marché en range » -------------------------------------------------------
    if _crosses(fast, slow, i, cfg.range_window) > cfg.max_crosses:
        return None
    # ---- filtre « grosses bougies » ---------------------------------------------------------
    rng = (df["high"].astype(float) - df["low"].astype(float))
    base = rng.iloc[max(0, i - cfg.big_candle_window - 1): i - 1].mean()
    if base > 0 and max(rng.iloc[i], rng.iloc[i - 1]) > cfg.big_candle_mult * base:
        return None

    direction = "call" if call_ok else "put"
    age = up_age if call_ok else dn_age
    gap = abs(fast.iloc[i] - slow.iloc[i]) / max(abs(slow.iloc[i]), 1e-12) * 10000
    score = 65 + (10 if age == 0 else 5) + min(15, round(gap))
    score = int(max(0, min(100, score)))
    up = direction == "call"
    reason = (f"SMA{cfg.ma_fast_period} croise SMA{cfg.ma_slow_period} à la {'hausse' if up else 'baisse'} "
              f"(marché en tendance, sans grosse bougie)")
    return Signal("5M", pair, direction, score, cfg.expiration, reason, float(close.iloc[i]))
