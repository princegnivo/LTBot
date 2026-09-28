"""
indicators.py
-------------
Indicateurs techniques utilisés par les stratégies du bot.
Toutes les fonctions travaillent sur un DataFrame pandas avec les colonnes
standard de l'API PocketOption : time, open, high, low, close.

Aucune dépendance externe type talib : tout est calculé en pandas/numpy pur
pour éviter les soucis d'installation (talib nécessite un binaire natif).
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Heikin Ashi
# ---------------------------------------------------------------------------
def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convertit un DataFrame OHLC classique en bougies Heikin Ashi.
    Retourne un nouveau DataFrame avec les colonnes ha_open, ha_high,
    ha_low, ha_close (les colonnes d'origine sont conservées).
    """
    ha = df.copy().reset_index(drop=True)
    ha_close = (ha["open"] + ha["high"] + ha["low"] + ha["close"]) / 4.0

    ha_open = np.zeros(len(ha))
    ha_open[0] = (ha["open"].iloc[0] + ha["close"].iloc[0]) / 2.0
    for i in range(1, len(ha)):
        ha_open[i] = (ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0

    ha_high = pd.concat(
        [ha["high"], pd.Series(ha_open, index=ha.index), ha_close], axis=1
    ).max(axis=1)
    ha_low = pd.concat(
        [ha["low"], pd.Series(ha_open, index=ha.index), ha_close], axis=1
    ).min(axis=1)

    ha["ha_open"] = ha_open
    ha["ha_high"] = ha_high
    ha["ha_low"] = ha_low
    ha["ha_close"] = ha_close
    return ha


# ---------------------------------------------------------------------------
# Moyennes mobiles
# ---------------------------------------------------------------------------
def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------
def bollinger_bands(series: pd.Series, period: int, std_dev: float):
    """
    Retourne (bande_basse, moyenne_mobile, bande_haute) sous forme de Series.
    """
    mid = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return lower, mid, upper


# ---------------------------------------------------------------------------
# RSI (Wilder)
# ---------------------------------------------------------------------------
def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    out[avg_loss == 0] = 100.0
    out[(avg_gain == 0) & (avg_loss == 0)] = 50.0
    return out


# ---------------------------------------------------------------------------
# Williams %R
# ---------------------------------------------------------------------------
def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """
    Williams %R classique, entre -100 (survente extrême) et 0 (surachat extrême).
    %R = (plus_haut_N - close) / (plus_haut_N - plus_bas_N) * -100
    """
    highest_high = high.rolling(window=period, min_periods=period).max()
    lowest_low = low.rolling(window=period, min_periods=period).min()
    rng = (highest_high - lowest_low).replace(0, np.nan)
    wr = (highest_high - close) / rng * -100.0
    return wr


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------
def macd(series: pd.Series, fast: int, slow: int, signal: int):
    """
    Retourne (macd_line, signal_line, histogram) sous forme de Series.
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


# ---------------------------------------------------------------------------
# Utilitaires bougie
# ---------------------------------------------------------------------------
def candle_body_ratio(open_: float, close_: float, high_: float, low_: float) -> float:
    """Ratio corps / range de la bougie (0 à 1)."""
    rng = high_ - low_
    if rng <= 0:
        return 0.0
    return abs(close_ - open_) / rng


def upper_wick(open_: float, close_: float, high_: float) -> float:
    return high_ - max(open_, close_)


def lower_wick(open_: float, close_: float, low_: float) -> float:
    return min(open_, close_) - low_


def candle_range(high_: float, low_: float) -> float:
    return max(high_ - low_, 1e-9)
