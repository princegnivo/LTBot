"""
strategy_1m.py
--------------
Stratégie "1M".

Bougies Heikin Ashi M1, expiration 60 secondes.
Calculs sur les closes Heikin Ashi :
    - Bollinger Bands : période 20, écart-type 2
    - SMA 2 et SMA 5
    - RSI 8, seuils 30 / 70

CALL si les 3 conditions sont vraies sur la dernière bougie :
    1. La bougie touche ou casse la bande inférieure de Bollinger
    2. SMA 2 croise SMA 5 vers le haut (entre i-1 et i)
    3. Le RSI sort de survente : il était < 30 dans les 3 dernières bougies,
       il remonte, et reste < 50

PUT si symétrique côté haut :
    1. Touche/casse la bande supérieure
    2. SMA 2 croise SMA 5 vers le bas
    3. RSI sort de surachat : était > 70 récemment, redescend, reste > 50

Score de confiance 0-100 : base 60, bonus sur corps de bougie fort,
profondeur du RSI, écart entre les SMA, pénétration de la bande. Plafond 100.

Pas de martingale sur cette stratégie (trade simple, mise fixe).
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import indicators as ind
from strategies.signal import Signal


@dataclass
class Strategy1mConfig:
    expiration: int = 60
    bb_period: int = 20
    bb_stddev: float = 2.0
    sma_fast: int = 2
    sma_slow: int = 5
    rsi_period: int = 8
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    rsi_exit_ceiling: float = 50.0
    lookback_rsi_extreme: int = 3
    min_candles: int = 30


def _min_periods_ok(df: pd.DataFrame, cfg: Strategy1mConfig) -> bool:
    needed = max(cfg.bb_period, cfg.sma_slow, cfg.rsi_period) + cfg.lookback_rsi_extreme + 2
    return len(df) >= max(needed, cfg.min_candles)


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy1mConfig] = None) -> Optional[Signal]:
    cfg = cfg or Strategy1mConfig()

    if not _min_periods_ok(df, cfg):
        return None

    ha = ind.heikin_ashi(df)
    close = ha["ha_close"]

    lower, mid, upper = ind.bollinger_bands(close, cfg.bb_period, cfg.bb_stddev)
    sma_fast = ind.sma(close, cfg.sma_fast)
    sma_slow = ind.sma(close, cfg.sma_slow)
    rsi_series = ind.rsi(close, cfg.rsi_period)

    i = len(ha) - 1
    if pd.isna(lower.iloc[i]) or pd.isna(sma_fast.iloc[i - 1]) or pd.isna(rsi_series.iloc[i]):
        return None

    o, h, l, c = (
        ha["ha_open"].iloc[i],
        ha["ha_high"].iloc[i],
        ha["ha_low"].iloc[i],
        ha["ha_close"].iloc[i],
    )
    rsi_window = rsi_series.iloc[max(0, i - cfg.lookback_rsi_extreme + 1): i + 1]

    # ---- Conditions CALL --------------------------------------------------
    touches_lower_band = c <= lower.iloc[i] or l <= lower.iloc[i]
    sma_cross_up = sma_fast.iloc[i - 1] <= sma_slow.iloc[i - 1] and sma_fast.iloc[i] > sma_slow.iloc[i]
    was_oversold = (rsi_window < cfg.rsi_oversold).any()
    rsi_recovering_up = rsi_series.iloc[i] > rsi_series.iloc[i - 1]
    rsi_stays_below_ceiling = rsi_series.iloc[i] < cfg.rsi_exit_ceiling
    call_ok = touches_lower_band and sma_cross_up and was_oversold and rsi_recovering_up and rsi_stays_below_ceiling

    # ---- Conditions PUT -----------------------------------------------------
    touches_upper_band = c >= upper.iloc[i] or h >= upper.iloc[i]
    sma_cross_down = sma_fast.iloc[i - 1] >= sma_slow.iloc[i - 1] and sma_fast.iloc[i] < sma_slow.iloc[i]
    was_overbought = (rsi_window > cfg.rsi_overbought).any()
    rsi_recovering_down = rsi_series.iloc[i] < rsi_series.iloc[i - 1]
    rsi_stays_above_floor = rsi_series.iloc[i] > cfg.rsi_exit_ceiling
    put_ok = touches_upper_band and sma_cross_down and was_overbought and rsi_recovering_down and rsi_stays_above_floor

    if not call_ok and not put_ok:
        return None

    direction = "call" if call_ok else "put"
    rng = ind.candle_range(h, l)

    # ---- Score de confiance --------------------------------------------------
    score = 60
    body_ratio = ind.candle_body_ratio(o, c, h, l)
    score += min(15, round(body_ratio * 15))

    if direction == "call":
        rsi_depth = max(0.0, cfg.rsi_oversold - rsi_window.min())
        band_penetration = max(0.0, (lower.iloc[i] - min(c, l)) / max(rng, 1e-9) * 100)
    else:
        rsi_depth = max(0.0, rsi_window.max() - cfg.rsi_overbought)
        band_penetration = max(0.0, (max(c, h) - upper.iloc[i]) / max(rng, 1e-9) * 100)

    score += min(10, round(rsi_depth))
    sma_gap = abs(sma_fast.iloc[i] - sma_slow.iloc[i]) / max(close.iloc[i], 1e-9) * 1000
    score += min(10, round(sma_gap))
    score += min(5, round(band_penetration / 10))
    score = int(max(0, min(100, score)))

    return Signal(
        strategy="1M",
        pair=pair,
        direction=direction,
        confidence=score,
        expiration=cfg.expiration,
        reason="Bollinger touchée/cassée + croisement SMA2/SMA5 + sortie de zone RSI",
        price=float(df["close"].iloc[-1]),
    )
