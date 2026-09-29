"""
strategy_2m.py
--------------
Stratégie "2M" — Bollinger Band + MACD sur bougies Heikin Ashi de 120
secondes (comme précisé), expiration 120 secondes.

Calculs sur les clôtures Heikin Ashi :
    - Bollinger Bands : période 6, écart-type 1.3
    - MACD : fast 6, slow 19, signal 6

Interprétation MACD (standard) : croisement haussier = la ligne MACD
passe AU-DESSUS de sa ligne de signal -> CALL. Croisement baissier = la
ligne MACD passe EN-DESSOUS de sa ligne de signal -> PUT.

CALL si les 3 conditions sont vraies :
    1. Croisement MACD haussier dans les 3 dernières bougies
    2. Bougie HA proche ou dépasse la bande supérieure de Bollinger
       (distance <= 15% de l'amplitude de la bande)
    3. Bougie HA haussière "forte et stable" :
        - corps >= 60% du range (high - low)
        - corps >= 1.5 x moyenne des corps des 10 dernières bougies

PUT symétrique côté bas.

Pas de martingale sur cette stratégie : chaque confirmation ne donne
lieu qu'à un seul trade en mise simple (voir auto_trader.py).
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
    crossover_lookback: int = 3        # bougies dans lesquelles chercher le croisement
    band_distance_pct: float = 15.0      # % de l'amplitude de la bande
    body_ratio_min: float = 0.60           # corps >= 60% du range
    body_strength_mult: float = 1.5          # corps >= 1.5x moyenne des 10 dernières
    body_avg_window: int = 10
    min_candles: int = 30


def _min_periods_ok(df: pd.DataFrame, cfg: Strategy2mConfig) -> bool:
    needed = max(cfg.bb_period, cfg.macd_slow) + cfg.body_avg_window + cfg.crossover_lookback + 2
    return len(df) >= max(needed, cfg.min_candles)


def _find_crossover_age(macd_line: pd.Series, signal_line: pd.Series, i: int, lookback: int, direction: str):
    """
    Cherche, dans les `lookback` dernières bougies (0 = bougie actuelle),
    l'âge en bougies du dernier croisement dans la direction demandée
    ("up" ou "down"). Retourne None si aucun croisement trouvé.
    """
    for age in range(0, lookback):
        idx = i - age
        if idx - 1 < 0:
            break
        prev_diff = macd_line.iloc[idx - 1] - signal_line.iloc[idx - 1]
        cur_diff = macd_line.iloc[idx] - signal_line.iloc[idx]
        if direction == "up" and prev_diff <= 0 and cur_diff > 0:
            return age
        if direction == "down" and prev_diff >= 0 and cur_diff < 0:
            return age
    return None


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy2mConfig] = None) -> Optional[Signal]:
    cfg = cfg or Strategy2mConfig()

    if not _min_periods_ok(df, cfg):
        return None

    ha = ind.heikin_ashi(df)
    close = ha["ha_close"]

    lower, mid, upper = ind.bollinger_bands(close, cfg.bb_period, cfg.bb_stddev)
    macd_line, signal_line, hist = ind.macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)

    i = len(ha) - 1
    if pd.isna(lower.iloc[i]) or pd.isna(macd_line.iloc[i]) or pd.isna(hist.iloc[i - 1]):
        return None

    o, h, l, c = (
        ha["ha_open"].iloc[i],
        ha["ha_high"].iloc[i],
        ha["ha_low"].iloc[i],
        ha["ha_close"].iloc[i],
    )
    rng = ind.candle_range(h, l)
    band_amplitude = max(upper.iloc[i] - lower.iloc[i], 1e-9)

    body_ratio = ind.candle_body_ratio(o, c, h, l)
    bodies = (ha["ha_close"] - ha["ha_open"]).abs()
    avg_body = bodies.iloc[max(0, i - cfg.body_avg_window): i].mean()
    is_bullish_candle = c > o
    is_bearish_candle = c < o
    strong_stable = body_ratio >= cfg.body_ratio_min and (avg_body == 0 or abs(c - o) >= cfg.body_strength_mult * avg_body)

    # ---- CALL ---------------------------------------------------------------
    call_age = _find_crossover_age(macd_line, signal_line, i, cfg.crossover_lookback, "up")
    call_band_ok = (upper.iloc[i] - c) / band_amplitude * 100 <= cfg.band_distance_pct
    call_ok = call_age is not None and call_band_ok and is_bullish_candle and strong_stable

    # ---- PUT ------------------------------------------------------------------
    put_age = _find_crossover_age(macd_line, signal_line, i, cfg.crossover_lookback, "down")
    put_band_ok = (c - lower.iloc[i]) / band_amplitude * 100 <= cfg.band_distance_pct
    put_ok = put_age is not None and put_band_ok and is_bearish_candle and strong_stable

    if not call_ok and not put_ok:
        return None

    direction = "call" if call_ok else "put"
    age = call_age if call_ok else put_age

    # ---- Score de confiance ---------------------------------------------------
    score = 60
    if age == 0:
        score += 10
    elif age == 1:
        score += 5

    hist_growing = hist.iloc[i] > hist.iloc[i - 1]
    hist_right_direction = (direction == "call" and hist.iloc[i] > 0) or (direction == "put" and hist.iloc[i] < 0)
    if hist_right_direction and hist_growing:
        score += 8

    if body_ratio >= 0.75:
        score += 6

    score = int(max(0, min(100, score)))

    reason = (
        f"Croisement MACD {'haussier' if direction == 'call' else 'baissier'} "
        f"(il y a {age} bougie(s)) + proximité bande Bollinger + bougie forte et stable"
    )

    return Signal(
        strategy="2M",
        pair=pair,
        direction=direction,
        confidence=score,
        expiration=cfg.expiration,
        reason=reason,
        price=float(c),
    )
