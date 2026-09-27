"""
strategy_5s.py
--------------
Stratégie "5s" (avec martingale).

Bougies NORMALES (pas Heikin Ashi) de 5 secondes, expiration 5 secondes.
Indicateur : RSI 14.

Logique du signal :
    - Le RSI passe en dessous de 30 (sortie/entrée de survente) -> signal
      CALL candidat (retournement haussier attendu).
    - Le RSI passe au-dessus de 70 (sortie/entrée de surachat) -> signal
      PUT candidat (retournement baissier attendu).

Confirmation par la mèche de la dernière bougie (paramètre "Pourcentage
d'ombre", shadow_percentage, 30% par défaut ; mettre à 0 pour désactiver
le filtre) :
    - Signal CALL confirmé si la mèche INFÉRIEURE de la dernière bougie
      représente au moins shadow_percentage % du range de la bougie.
    - Signal PUT confirmé si la mèche SUPÉRIEURE de la dernière bougie
      représente au moins shadow_percentage % du range de la bougie.

Score de confiance 0-100 : base 60, bonus sur la profondeur du RSI
(distance au seuil) et sur la taille de la mèche de confirmation.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import indicators as ind
from strategies.signal import Signal


@dataclass
class Strategy5sConfig:
    expiration: int = 5
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    shadow_percentage: float = 30.0   # % du range de la bougie ; 0 = filtre désactivé
    min_candles: int = 30


def _min_periods_ok(df: pd.DataFrame, cfg: Strategy5sConfig) -> bool:
    return len(df) >= max(cfg.rsi_period + 2, cfg.min_candles)


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy5sConfig] = None) -> Optional[Signal]:
    """
    df : DataFrame OHLC de bougies NORMALES de 5s (colonnes time, open,
    high, low, close), trié par temps croissant.
    """
    cfg = cfg or Strategy5sConfig()

    if not _min_periods_ok(df, cfg):
        return None

    rsi_series = ind.rsi(df["close"], cfg.rsi_period)

    i = len(df) - 1
    if pd.isna(rsi_series.iloc[i]) or pd.isna(rsi_series.iloc[i - 1]):
        return None

    o, h, l, c = (
        df["open"].iloc[i],
        df["high"].iloc[i],
        df["low"].iloc[i],
        df["close"].iloc[i],
    )
    rng = ind.candle_range(h, l)

    rsi_prev = rsi_series.iloc[i - 1]
    rsi_cur = rsi_series.iloc[i]

    # ---- Signal RSI candidat --------------------------------------------------
    call_candidate = rsi_prev >= cfg.rsi_oversold and rsi_cur < cfg.rsi_oversold
    put_candidate = rsi_prev <= cfg.rsi_overbought and rsi_cur > cfg.rsi_overbought

    if not call_candidate and not put_candidate:
        return None

    # ---- Confirmation par la mèche ---------------------------------------------
    lower_wick_ratio = (ind.lower_wick(o, c, l) / rng) * 100
    upper_wick_ratio = (ind.upper_wick(o, c, h) / rng) * 100

    if call_candidate:
        confirmed = cfg.shadow_percentage <= 0 or lower_wick_ratio >= cfg.shadow_percentage
        if not confirmed:
            return None
        direction = "call"
        wick_ratio = lower_wick_ratio
        rsi_depth = cfg.rsi_oversold - rsi_cur
    else:
        confirmed = cfg.shadow_percentage <= 0 or upper_wick_ratio >= cfg.shadow_percentage
        if not confirmed:
            return None
        direction = "put"
        wick_ratio = upper_wick_ratio
        rsi_depth = rsi_cur - cfg.rsi_overbought

    # ---- Score de confiance ---------------------------------------------------
    score = 60
    score += min(20, round(max(0.0, rsi_depth) * 2))
    if cfg.shadow_percentage > 0:
        score += min(20, round((wick_ratio - cfg.shadow_percentage) / 2))
    score = int(max(0, min(100, score)))

    if cfg.shadow_percentage > 0:
        reason = (
            f"RSI14 {'sort de survente (<30)' if direction == 'call' else 'sort de surachat (>70)'} "
            f"+ mèche de confirmation ({wick_ratio:.0f}%)"
        )
    else:
        reason = f"RSI14 {'sort de survente (<30)' if direction == 'call' else 'sort de surachat (>70)'}"

    return Signal(
        strategy="5s",
        pair=pair,
        direction=direction,
        confidence=score,
        expiration=cfg.expiration,
        reason=reason,
        price=float(c),
    )
