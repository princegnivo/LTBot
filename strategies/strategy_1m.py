"""
strategy_1m.py
--------------
Stratégie "1M" — Bollinger + 2 moyennes mobiles + RSI, sur bougies Heikin Ashi de 1 minute
(M1), expiration 1 minute.

Réglages :
    - Bandes de Bollinger : période 20, déviation 2
    - SMA 1 : période 2   (rapide)
    - SMA 2 : période 5   (lente)
    - RSI : période 8, surachat 70 / survente 30
    (pas de Williams %R)
Tous les indicateurs sont calculés sur les bougies Heikin Ashi.

CALL si, sur la dernière bougie HA :
    1. SMA2 croise SMA5 à la hausse (dans les 3 dernières bougies)
    2. Le prix a touché la bande basse de Bollinger dans les 5 dernières bougies
    3. Le RSI(8) était <= 30 dans les 5 dernières bougies et repasse au-dessus de 30
    4. Bougie HA haussière avec un corps >= 55 % du range
PUT symétrique : croisement à la baisse, contact de la bande haute, RSI(8) >= 70 puis retour sous 70.
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
    ma_fast_period: int = 2          # SMA 1
    ma_slow_period: int = 5          # SMA 2
    rsi_period: int = 8
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    cross_lookback: int = 3          # bougies dans lesquelles chercher le croisement des 2 SMA
    band_touch_lookback: int = 5     # bougies dans lesquelles chercher le contact avec la bande
    rsi_lookback: int = 5            # bougies dans lesquelles chercher le passage en zone extrême
    body_ratio_min: float = 0.55     # corps >= 55 % du range
    min_candles: int = 35


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy1mConfig] = None) -> Optional[Signal]:
    """df : bougies normales de 60 s (time, open, high, low, close) ; converties ici en Heikin Ashi."""
    cfg = cfg or Strategy1mConfig()
    if len(df) < max(cfg.min_candles, cfg.bb_period + 5):
        return None

    ha = ind.heikin_ashi(df)
    close, high, low, open_ = ha["ha_close"], ha["ha_high"], ha["ha_low"], ha["ha_open"]
    lower, _, upper = ind.bollinger_bands(close, cfg.bb_period, cfg.bb_stddev)
    ma_fast = ind.sma(close, cfg.ma_fast_period)
    ma_slow = ind.sma(close, cfg.ma_slow_period)
    rsi = ind.rsi(close, cfg.rsi_period)

    i = len(ha) - 1
    if pd.isna(lower.iloc[i]) or pd.isna(ma_slow.iloc[i]) or pd.isna(rsi.iloc[i]):
        return None

    o, h, l, c = open_.iloc[i], high.iloc[i], low.iloc[i], close.iloc[i]
    body_ratio = ind.candle_body_ratio(o, c, h, l)
    strong = body_ratio >= cfg.body_ratio_min

    win_b = slice(max(0, i - cfg.band_touch_lookback + 1), i + 1)
    win_r = rsi.iloc[max(0, i - cfg.rsi_lookback + 1): i + 1]
    rsi_cur = rsi.iloc[i]

    call_age = ind.cross_age(ma_fast, ma_slow, i, cfg.cross_lookback, "up")
    touched_lower = bool((low.iloc[win_b] <= lower.iloc[win_b]).any())
    rsi_exit_low = bool((win_r <= cfg.rsi_oversold).any() and rsi_cur > cfg.rsi_oversold)
    call_ok = call_age is not None and touched_lower and rsi_exit_low and c > o and strong

    put_age = ind.cross_age(ma_fast, ma_slow, i, cfg.cross_lookback, "down")
    touched_upper = bool((high.iloc[win_b] >= upper.iloc[win_b]).any())
    rsi_exit_high = bool((win_r >= cfg.rsi_overbought).any() and rsi_cur < cfg.rsi_overbought)
    put_ok = put_age is not None and touched_upper and rsi_exit_high and c < o and strong

    if not call_ok and not put_ok:
        return None
    direction = "call" if call_ok else "put"
    age = call_age if call_ok else put_age

    score = 60
    score += 10 if age == 0 else 5 if age == 1 else 0
    depth = (cfg.rsi_oversold - win_r.min()) if direction == "call" else (win_r.max() - cfg.rsi_overbought)
    score += min(12, round(max(0.0, depth)))
    score += min(15, round(body_ratio * 15))
    score = int(max(0, min(100, score)))

    up = direction == "call"
    reason = (f"SMA{cfg.ma_fast_period} croise SMA{cfg.ma_slow_period} à la {'hausse' if up else 'baisse'} + "
              f"contact bande Bollinger {'basse' if up else 'haute'} + "
              f"RSI{cfg.rsi_period} sort de {'survente' if up else 'surachat'} + bougie HA forte")
    return Signal("1M", pair, direction, score, cfg.expiration, reason, float(c))
