"""
Wrapper autour de pocketoptionapi.stable_api.PocketOption.

⚠️ Je n'ai vu que le README de la lib (pas le code source de stable_api.py /
pocket.py), donc le format exact renvoyé par GetHistory()/Buy()/CheckWin()
est déduit de l'exemple du README, pas garanti à 100%. Teste chaque méthode
isolément (prints) avant de lancer la boucle complète, et ajuste
_normalize_candles() / place_trade() / check_result() si le format diffère.
"""

import asyncio

from pocketoptionapi.stable_api import PocketOption


def _ensure_event_loop():
    """
    pocketoptionapi utilise asyncio.get_event_loop() à l'ancienne, qui ne crée
    plus de boucle automatiquement sur les versions récentes de Python
    (3.12+, et encore plus strict en 3.14). On en crée une explicitement
    avant d'instancier PocketOption pour éviter le RuntimeError.
    """
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


class TradingEngine:
    def __init__(self, demo=True, ssid=None):
        if ssid:
            # La version historique de la lib lit le SSID depuis global_value.SSID
            # (utile si tu veux éviter le login interactif via pywebview).
            try:
                import pocketoptionapi.global_value as global_value
                global_value.SSID = ssid
            except ImportError:
                pass

        _ensure_event_loop()
        self.api = PocketOption(demo)

    def connect(self):
        self.api.connect()

    def get_balance(self):
        return self.api.GetBalance()

    def get_pairs(self):
        return self.api.GetPairs()

    def get_candles(self, symbol, period=5):
        """Retourne une liste de dicts {open, high, low, close, time}."""
        raw = self.api.GetHistory(symbol)
        return self._normalize_candles(raw)

    @staticmethod
    def _normalize_candles(raw):
        candles = []
        for c in raw:
            # adapte ces clés si le format réel diffère (à vérifier à l'usage)
            candles.append(
                {
                    "open": c.get("open"),
                    "high": c.get("high"),
                    "low": c.get("low"),
                    "close": c.get("close"),
                    "time": c.get("time"),
                }
            )
        return candles

    def place_trade(self, symbol, direction, amount, duration_sec):
        """direction: 'call' | 'put'. Retourne (status, trade_id)."""
        status, trade_id = self.api.Buy(amount, symbol, direction, duration_sec)
        return status, trade_id

    def check_result(self, trade_id):
        """Retourne (won: bool, profit: float)."""
        profit, status = self.api.CheckWin(trade_id)
        won = profit is not None and profit > 0
        return won, profit
