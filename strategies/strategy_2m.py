"""
strategy_2m.py
--------------
Stratégie "2M" — Bollinger Bands + MACD, bougies NORMALES de 2 minutes (M2), expiration 2 minutes.
Réglages relevés dans la vidéo :
    - Bandes de Bollinger : période 6, déviation 1,3
    - MACD : rapide 6, lente 19, signal 6

Règles (vidéo) :
    HIGHER (CALL) : les lignes MACD se croisent à la hausse SOUS les barres (croisement sous zéro) et la
                    bougie de l'entrée (verte) est très proche de la ligne HAUTE de Bollinger.
    LOWER  (PUT)  : les lignes MACD se croisent à la baisse AU-DESSUS des barres (croisement au-dessus de
                    zéro) et la bougie de l'entrée (rouge) est très proche de la ligne BASSE de Bollinger.
Le croisement doit être récent (bougie actuelle ou précédente).
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import indicators as ind
from strategies.signal import Signal


@dataclass
class Strategy2mConfig:
    expiration: int = 120
    bb_period: int = 6
    bb_stddev: float = 1.3
    macd_fast: int = 6
    macd_slow: int = 19
    macd_signal: int = 6
    cross_lookback: int = 2          # croisement sur la bougie actuelle (0) ou la précédente (1)
    band_proximity: float = 0.20     # « très proche » = clôture dans les 20 % de la largeur de bande
    min_candles: int = 40


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy2mConfig] = None) -> Optional[Signal]:
    """df : bougies normales de 120 s (time, open, high, low, close)."""
    cfg = cfg or Strategy2mConfig()
    if len(df) < cfg.min_candles:
        return None

    close = df["close"].astype(float)
    lower, _, upper = ind.bollinger_bands(close, cfg.bb_period, cfg.bb_stddev)
    macd_line, sig_line, _ = ind.macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)

    i = len(df) - 1
    if pd.isna(lower.iloc[i]) or pd.isna(upper.iloc[i]):
        return None
    o, c = float(df["open"].iloc[i]), float(close.iloc[i])
    width = float(upper.iloc[i] - lower.iloc[i])
    if width <= 0:
        return None
    near_upper = c >= upper.iloc[i] - cfg.band_proximity * width
    near_lower = c <= lower.iloc[i] + cfg.band_proximity * width

    up_age = ind.cross_age(macd_line, sig_line, i, cfg.cross_lookback, "up")
    dn_age = ind.cross_age(macd_line, sig_line, i, cfg.cross_lookback, "down")
    call_ok = (up_age is not None and macd_line.iloc[i - up_age] < 0 and macd_line.iloc[i] > sig_line.iloc[i]
               and c > o and near_upper)
    put_ok = (dn_age is not None and macd_line.iloc[i - dn_age] > 0 and macd_line.iloc[i] < sig_line.iloc[i]
              and c < o and near_lower)
    if not call_ok and not put_ok:
        return None
    direction = "call" if call_ok else "put"
    age = up_age if call_ok else dn_age

    score = 65
    score += 10 if age == 0 else 5
    beyond = (c - upper.iloc[i]) if call_ok else (lower.iloc[i] - c)
    score += 10 if beyond >= 0 else 5
    score += min(10, round(ind.candle_body_ratio(o, c, df["high"].iloc[i], df["low"].iloc[i]) * 10))
    score = int(max(0, min(100, score)))

    up = direction == "call"
    reason = (f"MACD {cfg.macd_fast}/{cfg.macd_slow}/{cfg.macd_signal} croise à la {'hausse sous' if up else 'baisse au-dessus des'} "
              f"{'les barres' if up else 'barres'} + bougie proche de la bande {'haute' if up else 'basse'} "
              f"Bollinger {cfg.bb_period}/{cfg.bb_stddev:g}")
    return Signal("2M", pair, direction, score, cfg.expiration, reason, c)
