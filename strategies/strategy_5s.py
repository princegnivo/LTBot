"""
strategy_5s.py
--------------
Stratégie "5s" : bougies NORMALES de 5 secondes, expiration 5 secondes. Deux confirmations qui ne se
mélangent jamais ; le RSI 14 décide laquelle est active :

    RSI14 > 70 ou < 30  ->  ANCIENNE stratégie seule (RSI + mèche)
    RSI14 entre 30 et 70 ->  NOUVELLE stratégie seule (Bollinger)

1) Ancienne (RSI 14, niveaux 30 / 70, mèche de confirmation 30 %)
    - le RSI passe sous 30  -> CALL candidat ; il passe au-dessus de 70 -> PUT candidat
    - confirmé si la mèche opposée de la bougie >= 30 % de son range

2) Nouvelle (Bollinger période 13, déviation 2)
    - une bougie CLÔTURE au-dessus de la bande haute  -> trade rapide de la bougie suivante à la BAISSE (PUT)
    - une bougie CLÔTURE sous la bande basse           -> trade rapide de la bougie suivante à la HAUSSE (CALL)
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
    bb_period: int = 13
    bb_stddev: float = 2.0
    min_candles: int = 30


def _rsi_wick(df: pd.DataFrame, pair: str, cfg: Strategy5sConfig, rsi_series: pd.Series) -> Optional[Signal]:
    """Ancienne confirmation : RSI qui sort de sa zone + mèche de confirmation."""
    i = len(df) - 1
    if pd.isna(rsi_series.iloc[i - 1]):
        return None
    o, h, l, c = df["open"].iloc[i], df["high"].iloc[i], df["low"].iloc[i], df["close"].iloc[i]
    rng = ind.candle_range(h, l)
    rsi_prev, rsi_cur = rsi_series.iloc[i - 1], rsi_series.iloc[i]

    call_candidate = rsi_prev >= cfg.rsi_oversold and rsi_cur < cfg.rsi_oversold
    put_candidate = rsi_prev <= cfg.rsi_overbought and rsi_cur > cfg.rsi_overbought
    if not call_candidate and not put_candidate:
        return None

    lower_wick_ratio = ind.lower_wick(o, c, l) / rng * 100
    upper_wick_ratio = ind.upper_wick(o, c, h) / rng * 100
    if call_candidate:
        if cfg.shadow_percentage > 0 and lower_wick_ratio < cfg.shadow_percentage:
            return None
        direction, wick_ratio, rsi_depth = "call", lower_wick_ratio, cfg.rsi_oversold - rsi_cur
    else:
        if cfg.shadow_percentage > 0 and upper_wick_ratio < cfg.shadow_percentage:
            return None
        direction, wick_ratio, rsi_depth = "put", upper_wick_ratio, rsi_cur - cfg.rsi_overbought

    score = 60 + min(20, round(max(0.0, rsi_depth) * 2))
    if cfg.shadow_percentage > 0:
        score += min(20, round((wick_ratio - cfg.shadow_percentage) / 2))
    score = int(max(0, min(100, score)))
    reason = f"RSI{cfg.rsi_period} {'sort de survente (<30)' if direction == 'call' else 'sort de surachat (>70)'}"
    if cfg.shadow_percentage > 0:
        reason += f" + mèche de confirmation ({wick_ratio:.0f}%)"
    return Signal("5s", pair, direction, score, cfg.expiration, reason, float(c))


def _bollinger(df: pd.DataFrame, pair: str, cfg: Strategy5sConfig, forming: bool) -> Optional[Signal]:
    """Nouvelle confirmation : la bougie précédente (close) est sortie de la bande -> trade inverse sur la suivante."""
    j = len(df) - 2 if forming else len(df) - 1        # dernière bougie CLÔTURÉE ; l'entrée se fait sur la suivante
    if j < cfg.bb_period:
        return None
    close = df["close"].astype(float)
    lower, _, upper = ind.bollinger_bands(close, cfg.bb_period, cfg.bb_stddev)
    if pd.isna(upper.iloc[j]):
        return None
    c, up, lo = float(close.iloc[j]), float(upper.iloc[j]), float(lower.iloc[j])
    if c > up:
        direction, out = "put", c - up
    elif c < lo:
        direction, out = "call", lo - c
    else:
        return None
    width = max(up - lo, 1e-12)
    score = int(max(0, min(100, 60 + min(30, round(out / width * 100)))))
    reason = (f"Bollinger {cfg.bb_period}/{cfg.bb_stddev:g} : bougie sortie de la bande "
              f"{'haute' if direction == 'put' else 'basse'} → entrée inverse sur la bougie suivante (RSI neutre)")
    return Signal("5s", pair, direction, score, cfg.expiration, reason, float(df["close"].iloc[-1]))


def analyze(df: pd.DataFrame, pair: str, cfg: Optional[Strategy5sConfig] = None) -> Optional[Signal]:
    """
    df : bougies NORMALES de 5 s (time, open, high, low, close), triées par temps croissant.
    df.attrs["forming"] = True si la dernière ligne est la bougie en formation (mode évaluation en cours de bougie).
    """
    cfg = cfg or Strategy5sConfig()
    if len(df) < max(cfg.rsi_period + 2, cfg.min_candles):
        return None
    rsi_series = ind.rsi(df["close"], cfg.rsi_period)
    rsi_now = rsi_series.iloc[-1]
    if pd.isna(rsi_now):
        return None
    if rsi_now > cfg.rsi_overbought or rsi_now < cfg.rsi_oversold:     # zone RSI : l'ancienne stratégie a la priorité
        return _rsi_wick(df, pair, cfg, rsi_series)
    return _bollinger(df, pair, cfg, bool(df.attrs.get("forming", False)))
