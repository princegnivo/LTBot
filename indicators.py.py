"""
candles: liste de dicts {open, high, low, close, time}, du plus ancien au plus récent
(c'est le format retourné par api.GetHistory() / api.GetCandles() de PocketOptionAPI-v2 —
adapte le mapping des clés si le format exact diffère à l'usage)
"""


def compute_rsi(candles, period=14):
    if len(candles) < period + 1:
        return None

    gains = 0.0
    losses = 0.0

    for i in range(len(candles) - period, len(candles)):
        diff = candles[i]["close"] - candles[i - 1]["close"]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / period
    avg_loss = losses / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def analyze_wick(candle):
    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return 0.0, 0.0

    body_top = max(candle["open"], candle["close"])
    body_bottom = min(candle["open"], candle["close"])

    upper_wick = candle["high"] - body_top
    lower_wick = body_bottom - candle["low"]

    return (upper_wick / rng) * 100, (lower_wick / rng) * 100


def get_signal(candles, strategy_cfg):
    """Retourne (signal, rsi) où signal est 'call' | 'put' | None."""
    rsi = compute_rsi(candles, strategy_cfg["rsi_period"])
    if rsi is None:
        return None, None

    last_candle = candles[-1]
    upper_wick_pct, lower_wick_pct = analyze_wick(last_candle)
    wick_percent = strategy_cfg["wick_percent"]

    signal = None

    if rsi >= strategy_cfg["rsi_overbought"]:
        if wick_percent == 0 or upper_wick_pct >= wick_percent:
            signal = "put"  # retournement à la baisse
    elif rsi <= strategy_cfg["rsi_oversold"]:
        if wick_percent == 0 or lower_wick_pct >= wick_percent:
            signal = "call"  # retournement à la hausse

    return signal, rsi
