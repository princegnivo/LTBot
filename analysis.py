"""Lecture de tendance affichée dans les signaux (comme dans la vidéo : EMA9/EMA21 + MACD)."""
from typing import Dict

import pandas as pd

import indicators as ind


def trend(df: pd.DataFrame) -> Dict:
    if df is None or len(df) < 30:
        return {"dir": "neutral", "text": "données insuffisantes", "label": "neutre"}
    c = df["close"].astype(float)
    e9, e21 = ind.ema(c, 9).iloc[-1], ind.ema(c, 21).iloc[-1]
    hist = ind.macd(c, 12, 26, 9)[2].iloc[-1]
    up, dn = e9 > e21, e9 < e21
    macd_pos = hist > 0
    if up and macd_pos:
        d, label = "up", "haussière"
    elif dn and not macd_pos:
        d, label = "down", "baissière"
    else:
        d, label = "neutral", "neutre"
    text = f"EMA9{'>' if up else '<'}EMA21, MACD{'+' if macd_pos else '−'}"
    return {"dir": d, "text": text, "label": label}
