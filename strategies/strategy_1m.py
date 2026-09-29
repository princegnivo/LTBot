"""
strategy_1m.py
--------------
Stratégie "1M" — indicateurs Moving Average + Williams %R, sur bougies
Heikin Ashi de 60 secondes (comme précisé), expiration 60 secondes.

Indicateurs :
    - Moyenne mobile simple (SMA), période 5, calculée sur les clôtures
      Heikin Ashi
    - Williams %R, période 14 (calculé sur les high/low/close Heikin
      Ashi), niveaux de référence -20 (surachat) et -80 (survente)

Logique du signal — la bougie HA vient "tester" la moyenne mobile
pendant que le Williams %R sort d'une zone extrême, avec une bougie de
confirmation forte et stable (corps >= 55% du range) :

CALL si, sur la dernière bougie HA :
    1. Le prix touche ou passe sous la SMA5 — le marché "teste" la
       moyenne par le bas.
    2. Le Williams %R était <= -80 (survente) il y a peu (fenêtre de 3
       bougies) et remonte au-dessus de -80 sur la bougie courante
       (sortie de survente).
    3. La bougie HA courante est haussière (close > open) et son corps
       représente au moins 55% du range (bougie "forte et stable").

PUT symétrique côté haut (touche la SMA par le haut, Williams %R sort de
surachat en repassant sous -20, bougie baissière forte).

Score de confiance 0-100 : base 60, bonus sur la force du corps, la
profondeur atteinte par le Williams %R, et la proximité exacte avec la
moyenne mobile.

Pas de martingale propre à cette stratégie ici : la gestion de la
martingale (2 étapes maximum après une perte) est appliquée de façon
centralisée par l'auto-trader, quelle que soit la stratégie active.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import indicators as ind
from strategies.signal import Signal


@dataclass
class Strategy1mConfig:
    expiration: int = 60
    ma_period: int = 5
    wr_period: int = 14
    wr_overbought: float = -20.0   # niveau haut du Williams %R (proche de 0)
    wr_oversold: float = -80.0     # niveau bas du Williams %R (proche de -100)
    lookback_extreme: int = 3      # bougies dans lesquelles chercher le passage en zone extrême
    body_ratio_min: float = 0.55   # bougie de confirmation : corps >= 55% du range
    ma_touch_tolerance_pct: float = 0.05  # tolérance (% du range) pour considérer que le prix "touche" la SMA
    min_candles: int = 30


def _min_periods_ok(df: pd.DataFrame, cfg: Strategy1mConfig) -> bool:
    needed = max(cfg.ma_period, cfg.wr_period) + cfg.lookback_extreme + 2
    return len(df) >= max(needed, cfg.min_candles)


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy1mConfig] = None) -> Optional[Signal]:
    """
    df : DataFrame OHLC de bougies normales de 60s (colonnes time, open,
    high, low, close), trié par temps croissant. Converti ici en bougies
    Heikin Ashi avant analyse.
    """
    cfg = cfg or Strategy1mConfig()

    if not _min_periods_ok(df, cfg):
        return None

    ha = ind.heikin_ashi(df)
    close = ha["ha_close"]
    high = ha["ha_high"]
    low = ha["ha_low"]
    open_ = ha["ha_open"]

    ma = ind.sma(close, cfg.ma_period)
    wr = ind.williams_r(high, low, close, cfg.wr_period)

    i = len(ha) - 1
    if pd.isna(ma.iloc[i]) or pd.isna(wr.iloc[i - 1]):
        return None

    o, h, l, c = open_.iloc[i], high.iloc[i], low.iloc[i], close.iloc[i]
    rng = ind.candle_range(h, l)
    ma_now = ma.iloc[i]

    wr_window = wr.iloc[max(0, i - cfg.lookback_extreme + 1): i + 1]
    wr_cur = wr.iloc[i]
    wr_prev = wr.iloc[i - 1]

    body_ratio = ind.candle_body_ratio(o, c, h, l)
    is_bullish = c > o
    is_bearish = c < o
    strong_candle = body_ratio >= cfg.body_ratio_min

    tolerance = rng * cfg.ma_touch_tolerance_pct * 10  # tolérance généreuse autour de la MA

    # ---- Conditions CALL (test de la MA par le bas + sortie de survente) ----
    touches_ma_from_below = l <= ma_now + tolerance and c >= ma_now - tolerance
    was_oversold = (wr_window <= cfg.wr_oversold).any()
    wr_exiting_oversold = wr_prev <= cfg.wr_oversold and wr_cur > cfg.wr_oversold
    call_ok = touches_ma_from_below and was_oversold and wr_exiting_oversold and is_bullish and strong_candle

    # ---- Conditions PUT (test de la MA par le haut + sortie de surachat) ----
    touches_ma_from_above = h >= ma_now - tolerance and c <= ma_now + tolerance
    was_overbought = (wr_window >= cfg.wr_overbought).any()
    wr_exiting_overbought = wr_prev >= cfg.wr_overbought and wr_cur < cfg.wr_overbought
    put_ok = touches_ma_from_above and was_overbought and wr_exiting_overbought and is_bearish and strong_candle

    if not call_ok and not put_ok:
        return None

    direction = "call" if call_ok else "put"

    # ---- Score de confiance -----------------------------------------------
    score = 60
    score += min(15, round(body_ratio * 15))
    if direction == "call":
        wr_depth = max(0.0, cfg.wr_oversold - wr_window.min())
    else:
        wr_depth = max(0.0, wr_window.max() - cfg.wr_overbought)
    score += min(15, round(wr_depth / 3))
    ma_distance_pct = abs(c - ma_now) / max(rng, 1e-9) * 100
    score += min(10, round(max(0.0, 20 - ma_distance_pct) / 2))
    score = int(max(0, min(100, score)))

    reason = (
        f"Prix teste la MA{cfg.ma_period} par le "
        f"{'bas' if direction == 'call' else 'haut'} + Williams %R{cfg.wr_period} "
        f"sort de {'survente' if direction == 'call' else 'surachat'} "
        f"+ bougie forte ({body_ratio*100:.0f}% de corps)"
    )

    return Signal(
        strategy="1M",
        pair=pair,
        direction=direction,
        confidence=score,
        expiration=cfg.expiration,
        reason=reason,
        price=float(c),
    )
