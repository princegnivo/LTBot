"""
signal_engine.py
------------------
Boucle principale de scan : pour chaque paire suivie, récupère les bougies
au bon pas de temps pour chaque stratégie, fait tourner les 3 stratégies,
et dispatch chaque signal détecté selon le mode configuré pour la
stratégie concernée :
    - "manual" : notification Telegram uniquement
    - "auto"   : trade automatique uniquement (pas de notif de signal, juste le résultat)
    - "both"   : notification Telegram + trade automatique

Un signal n'est traité qu'une seule fois par bougie (déduplication via
l'horodatage de la dernière bougie close, par paire et par stratégie).

Pas de temps utilisés :
    - "5s"  -> bougies normales de  5 secondes (period=5)
    - "1M"  -> bougies Heikin Ashi de 60 secondes (period=60)
    - "2M"  -> bougies Heikin Ashi de 120 secondes (period=120)
"""

import time
from typing import Dict, Optional

import pandas as pd

import config
from strategies import strategy_5s, strategy_1m, strategy_2m
from strategies.signal import Signal
from telegram_bot import TelegramNotifier
from auto_trader import AutoTrader


class SignalEngine:
    def __init__(self, api, telegram: Optional[TelegramNotifier] = None, auto_trader: Optional[AutoTrader] = None):
        self.api = api
        self.telegram = telegram or TelegramNotifier()
        self.auto_trader = auto_trader or AutoTrader(api, self.telegram, config.MARTINGALE_CONFIG)
        self._last_seen: Dict[str, pd.Timestamp] = {}  # clé "pair|strategy" -> dernière bougie traitée

    def _already_processed(self, pair: str, strategy: str, df: pd.DataFrame) -> bool:
        key = f"{pair}|{strategy}"
        last_ts = df["time"].iloc[-1]
        if self._last_seen.get(key) == last_ts:
            return True
        self._last_seen[key] = last_ts
        return False

    def _dispatch(self, signal: Signal, mode: str):
        if signal.confidence < config.MIN_CONFIDENCE_TO_NOTIFY:
            return

        if mode in ("manual", "both"):
            self.telegram.send_signal(signal)

        if mode in ("auto", "both") and signal.confidence >= config.MIN_CONFIDENCE_TO_AUTOTRADE:
            self.auto_trader.handle_signal(signal)

    def _get_df(self, pair: str, period: int) -> Optional[pd.DataFrame]:
        df = self.api.get_dataframe(pair, period)
        if df is None or len(df) < 30:
            return None
        return df.sort_values("time").reset_index(drop=True)

    def scan_pair(self, pair: str):
        # --- Stratégie 5s : bougies normales de 5s ---
        df5 = self._get_df(pair, config.STRATEGY_5S_CONFIG.expiration)
        if df5 is not None and not self._already_processed(pair, "5s", df5):
            sig = strategy_5s.analyze(df5, pair, config.STRATEGY_5S_CONFIG)
            if sig:
                self._dispatch(sig, config.STRATEGY_5S_MODE)

        # --- Stratégie 1M : bougies Heikin Ashi de 60s ---
        df1m = self._get_df(pair, 60)
        if df1m is not None and not self._already_processed(pair, "1M", df1m):
            sig = strategy_1m.analyze(df1m, pair, config.STRATEGY_1M_CONFIG)
            if sig:
                self._dispatch(sig, config.STRATEGY_1M_MODE)

        # --- Stratégie 2M : bougies Heikin Ashi de 120s ---
        df2m = self._get_df(pair, 120)
        if df2m is not None and not self._already_processed(pair, "2M", df2m):
            sig = strategy_2m.analyze(df2m, pair, config.STRATEGY_2M_CONFIG)
            if sig:
                self._dispatch(sig, config.STRATEGY_2M_MODE)

    def run_forever(self):
        print("Moteur de signaux démarré. Ctrl+C pour arrêter.")
        while True:
            for pair in config.PAIRS:
                try:
                    self.scan_pair(pair.strip())
                except Exception as e:
                    print(f"[Scan] Erreur sur {pair} : {e}")
            time.sleep(config.SCAN_INTERVAL_SECONDS)
