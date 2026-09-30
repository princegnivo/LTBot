"""Indicateurs techniques (pandas/numpy purs) utilisés par les stratégies."""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)                      # aucune baisse -> 100
    out = out.where(~((avg_loss == 0.0) & (avg_gain == 0.0)), 50.0)  # marché plat -> 50
    return out.where(avg_gain.notna())


def bollinger_bands(close: pd.Series, period: int = 20, stddev: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return mid - stddev * std, mid, mid + stddev * std  # lower, mid, upper


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig  # macd, signal, histogramme


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return -100.0 * (hh - close) / (hh - ll).replace(0.0, np.nan)


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = (df[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
    ha_close = (o + h + l + c) / 4.0
    ha_open = np.empty_like(ha_close)
    if len(ha_close):
        ha_open[0] = (o[0] + c[0]) / 2.0
        for i in range(1, len(ha_close)):
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    ha_high = np.maximum.reduce([h, ha_open, ha_close])
    ha_low = np.minimum.reduce([l, ha_open, ha_close])
    return pd.DataFrame(
        {"ha_open": ha_open, "ha_high": ha_high, "ha_low": ha_low, "ha_close": ha_close},
        index=df.index,
    )


# ---- helpers de bougie (scalaires) -------------------------------------------
def candle_range(high: float, low: float) -> float:
    return max(float(high) - float(low), 1e-9)


def candle_body_ratio(o: float, c: float, h: float, l: float) -> float:
    return abs(float(c) - float(o)) / candle_range(h, l)


def lower_wick(o: float, c: float, l: float) -> float:
    return min(float(o), float(c)) - float(l)


def upper_wick(o: float, c: float, h: float) -> float:
    return float(h) - max(float(o), float(c))


def cross_age(fast: pd.Series, slow: pd.Series, i: int, lookback: int, direction: str):
    """Âge (en bougies, 0 = bougie actuelle) du dernier croisement `fast` / `slow` dans les `lookback`
    dernières bougies. direction : "up" (fast passe au-dessus) ou "down". None si aucun croisement."""
    for age in range(lookback):
        idx = i - age
        if idx - 1 < 0:
            break
        p = fast.iloc[idx - 1] - slow.iloc[idx - 1]
        c = fast.iloc[idx] - slow.iloc[idx]
        if pd.isna(p) or pd.isna(c):
            continue
        if direction == "up" and p <= 0 < c:
            return age
        if direction == "down" and p >= 0 > c:
            return age
    return None
