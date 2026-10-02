"""Tests des 4 stratégies : réglages relevés dans les vidéos, règles d'entrée, filtres, priorité RSI du 5s."""
import numpy as np
import pandas as pd

import indicators as ind
from strategies import REGISTRY, strategy_1m, strategy_2m, strategy_5m, strategy_5s


def synth(n, seed, vol=0.0005):
    r = np.random.default_rng(seed)
    c = 1 + np.cumsum(r.normal(0, vol, n))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + abs(r.normal(0, vol * 0.7, n))
    l = np.minimum(o, c) - abs(r.normal(0, vol * 0.7, n))
    return pd.DataFrame({"time": range(n), "open": o, "high": h, "low": l, "close": c})


def from_closes(closes, wick=0.0002):
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"time": range(len(c)), "open": o, "high": np.maximum(o, c) + wick,
                         "low": np.minimum(o, c) - wick, "close": c})


def test_settings_match_videos():
    a, b, c, d = (strategy_1m.Strategy1mConfig(), strategy_2m.Strategy2mConfig(),
                  strategy_5m.Strategy5mConfig(), strategy_5s.Strategy5sConfig())
    assert (a.bb_period, a.bb_stddev, a.ma_fast_period, a.ma_slow_period, a.rsi_period, a.rsi_overbought, a.rsi_oversold, a.expiration) == \
           (20, 2.0, 2, 5, 8, 70.0, 30.0, 60)
    assert (b.bb_period, b.bb_stddev, b.macd_fast, b.macd_slow, b.macd_signal, b.expiration) == (6, 1.3, 6, 19, 6, 120)
    assert (c.ma_fast_period, c.ma_slow_period, c.expiration) == (3, 50, 300)
    assert (d.bb_period, d.bb_stddev, d.rsi_period, d.expiration) == (13, 2.0, 14, 5)
    assert not hasattr(ind, "williams_r"), "Williams %R supprimé"
    assert REGISTRY["1M"].ha and not REGISTRY["2M"].ha and not REGISTRY["5M"].ha
    assert (REGISTRY["5M"].timeframe, REGISTRY["5M"].expiration) == (60, 300)
    assert (REGISTRY["2M"].timeframe, REGISTRY["2M"].expiration) == (120, 120)


def test_all_strategies_generate_both_directions():
    for key, info in REGISTRY.items():
        seen = set()
        for seed in range(250):
            df = synth(140, seed)
            for cut in range(75, 140, 3):
                d = df.iloc[:cut].reset_index(drop=True)
                d.attrs["forming"] = True
                s = info.analyze(d, "T_otc")
                if s:
                    assert s.strategy == key and s.expiration == info.expiration and 0 <= s.confidence <= 100
                    assert "Williams" not in s.reason
                    seen.add(s.direction)
        assert seen == {"call", "put"}, (key, seen)


def test_1m_signals_satisfy_every_condition():
    cfg, n = strategy_1m.Strategy1mConfig(), 0
    for seed in range(300):
        df = synth(120, seed)
        for cut in range(60, 120, 2):
            d = df.iloc[:cut].reset_index(drop=True)
            s = strategy_1m.analyze(d, "T")
            if not s:
                continue
            n += 1
            ha = ind.heikin_ashi(d)
            cl = ha["ha_close"]
            lo, _, up = ind.bollinger_bands(cl, 20, 2.0)
            f, sl, r = ind.sma(cl, 2), ind.sma(cl, 5), ind.rsi(cl, 8)
            i = len(ha) - 1
            if s.direction == "call":
                assert ind.cross_age(f, sl, i, 3, "up") is not None and ha["ha_close"].iloc[i] > ha["ha_open"].iloc[i]
                assert (ha["ha_low"].iloc[i - 4:i + 1] <= lo.iloc[i - 4:i + 1]).any()
                assert (r.iloc[i - 4:i + 1] <= 30).any() and r.iloc[i] > 30
            else:
                assert ind.cross_age(f, sl, i, 3, "down") is not None and ha["ha_close"].iloc[i] < ha["ha_open"].iloc[i]
                assert (ha["ha_high"].iloc[i - 4:i + 1] >= up.iloc[i - 4:i + 1]).any()
                assert (r.iloc[i - 4:i + 1] >= 70).any() and r.iloc[i] < 70
    assert n > 20, n


def test_2m_signals_follow_the_video_rules():
    n = 0
    for seed in range(300):
        df = synth(110, seed)
        for cut in range(60, 110, 2):
            d = df.iloc[:cut].reset_index(drop=True)
            s = strategy_2m.analyze(d, "T")
            if not s:
                continue
            n += 1
            cl = d["close"]
            lo, _, up = ind.bollinger_bands(cl, 6, 1.3)
            m, sg, _ = ind.macd(cl, 6, 19, 6)
            i, w = len(d) - 1, float(up.iloc[-1] - lo.iloc[-1])
            if s.direction == "call":      # croisement à la hausse SOUS zéro, bougie verte proche de la bande HAUTE
                age = ind.cross_age(m, sg, i, 2, "up")
                assert age is not None and m.iloc[i - age] < 0 and d["close"].iloc[i] > d["open"].iloc[i]
                assert cl.iloc[i] >= up.iloc[i] - 0.2 * w - 1e-12
            else:                           # croisement à la baisse AU-DESSUS de zéro, bougie rouge proche de la bande BASSE
                age = ind.cross_age(m, sg, i, 2, "down")
                assert age is not None and m.iloc[i - age] > 0 and d["close"].iloc[i] < d["open"].iloc[i]
                assert cl.iloc[i] <= lo.iloc[i] + 0.2 * w + 1e-12
    assert n > 20, n


def _first_signal(closes, **kw):
    df = from_closes(closes, **kw)
    for cut in range(60, len(df) + 1):
        s = strategy_5m.analyze(df.iloc[:cut].reset_index(drop=True), "T")
        if s:
            return cut, s
    return None, None


def test_5m_cross_up_and_down():
    down_up = np.r_[np.linspace(1.03, 1.0, 60), np.linspace(1.0, 1.04, 60)]
    cut, s = _first_signal(down_up)
    assert s is not None and s.direction == "call" and s.expiration == 300 and "SMA3" in s.reason
    up_down = np.r_[np.linspace(1.0, 1.03, 60), np.linspace(1.03, 0.99, 60)]
    cut, s = _first_signal(up_down)
    assert s is not None and s.direction == "put"


def test_5m_avoids_ranging_market():
    wave = 1.0 + 0.0012 * np.sin(np.arange(160) / 2.0)           # SMA3 traverse sans cesse la SMA50
    cut, s = _first_signal(wave)
    assert s is None, "marché en range : aucun signal"


def test_5m_avoids_big_candles():
    down_up = np.r_[np.linspace(1.03, 1.0, 60), np.linspace(1.0, 1.04, 60)]
    cut, s = _first_signal(down_up)
    df = from_closes(down_up[:cut])
    df.loc[len(df) - 1, "high"] += 0.02                              # grosse bougie sur le croisement
    assert strategy_5m.analyze(df, "T") is None


def _alt(n=40, d=0.001):
    return 1.0 + d * (np.arange(n) % 2)


def test_5s_bollinger_uses_next_candle_after_exit():
    c = list(_alt())
    c[-2] = 1.0035                      # bougie clôturée au-dessus de la bande haute
    c[-1] = 1.001                       # bougie suivante (en formation) : c'est elle qu'on trade
    df = from_closes(c); df.attrs["forming"] = True
    r = ind.rsi(df["close"], 14).iloc[-1]
    assert 30 <= r <= 70, r
    s = strategy_5s.analyze(df, "T")
    assert s and s.direction == "put" and s.expiration == 5 and "Bollinger" in s.reason
    # en dessous de la bande basse -> HAUSSE
    c = list(_alt()); c[-2] = 0.9975; c[-1] = 1.0
    df = from_closes(c); df.attrs["forming"] = True
    assert 30 <= ind.rsi(df["close"], 14).iloc[-1] <= 70
    s = strategy_5s.analyze(df, "T")
    assert s and s.direction == "call"
    # même série mais la bougie sortie est la DERNIÈRE clôturée (mode clôture) : signal immédiat
    df.attrs["forming"] = False
    c2 = list(_alt()); c2[-1] = 0.9975
    df2 = from_closes(c2)
    assert 30 <= ind.rsi(df2["close"], 14).iloc[-1] <= 70
    assert strategy_5s.analyze(df2, "T").direction == "call"
    # aucune sortie de bande -> rien
    assert strategy_5s.analyze(from_closes(_alt()), "T") is None


def test_5s_rsi_zone_leaves_the_floor_to_the_old_strategy():
    # chute régulière : RSI14 < 30 depuis longtemps ET dernière bougie hors bande -> Bollinger seule dirait HAUSSE…
    df = from_closes(np.linspace(1.02, 0.98, 40)); df.attrs["forming"] = False
    assert ind.rsi(df["close"], 14).iloc[-1] < 30
    df.loc[len(df) - 1, "close"] -= 0.003
    assert strategy_5s._bollinger(df, "T", strategy_5s.Strategy5sConfig(), False) is not None
    # …mais RSI en zone extrême : la nouvelle stratégie s'efface (pas de mélange) ; l'ancienne n'a pas de croisement -> rien
    assert strategy_5s.analyze(df, "T") is None


def test_5s_old_strategy_still_works_in_rsi_zone():
    # on cherche un cas où le RSI14 passe sous 30 sur la DERNIÈRE bougie (avec une longue mèche basse)
    for decline in range(4, 22):
        for extra in np.linspace(0.0003, 0.006, 30):
            base = list(np.r_[np.linspace(1.0, 1.01, 25), np.linspace(1.01, 1.0, decline)])
            df = from_closes(base + [base[-1] - extra], wick=0.0)
            df.loc[len(df) - 1, "low"] = df["close"].iloc[-1] - 0.003
            r = ind.rsi(df["close"], 14)
            if r.iloc[-2] >= 30 > r.iloc[-1]:
                s = strategy_5s.analyze(df, "T")
                assert s and s.direction == "call" and "RSI14" in s.reason and "survente" in s.reason
                df.loc[len(df) - 1, "low"] = df["close"].iloc[-1]          # sans mèche : filtre de confirmation -> rien
                assert strategy_5s.analyze(df, "T") is None
                return
    raise AssertionError("scénario RSI introuvable")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("✓", n)
