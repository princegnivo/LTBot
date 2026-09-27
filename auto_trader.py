"""
auto_trader.py
---------------
Place les trades automatiques sur l'API PocketOption à partir d'un Signal,
et gère la séquence de martingale pour la stratégie "5s".

Fonctionnement de la martingale ici : quand un trade "5s" perd, on relance
immédiatement un trade dans la MÊME direction avec la mise suivante
(x2.2), jusqu'à gagner ou atteindre max_steps. Chaque étape est notifiée
sur Telegram. Ce comportement tourne dans un thread séparé pour ne pas
bloquer le scan des autres paires.
"""

import threading

from strategies.martingale import MartingaleManager, MartingaleConfig
from strategies.signal import Signal
from telegram_bot import TelegramNotifier


class AutoTrader:
    def __init__(self, api, telegram: TelegramNotifier, martingale_config: MartingaleConfig):
        self.api = api
        self.telegram = telegram
        self.martingale_config = martingale_config
        # Une martingale indépendante par paire pour la stratégie 5s
        self._martingales = {}
        self._lock = threading.Lock()

    def _get_martingale(self, pair: str) -> MartingaleManager:
        with self._lock:
            if pair not in self._martingales:
                self._martingales[pair] = MartingaleManager(self.martingale_config)
            return self._martingales[pair]

    def handle_signal(self, signal: Signal):
        """Lance le traitement du signal dans un thread dédié."""
        thread = threading.Thread(target=self._run, args=(signal,), daemon=True)
        thread.start()

    def _place_trade(self, pair: str, direction: str, amount: float, expiration: int):
        try:
            status, order_id = self.api.Buy(amount, pair, direction, expiration)
            return status, order_id
        except Exception as e:
            self.telegram.send_error(f"Échec d'ouverture de trade {pair} {direction} : {e}")
            return False, None

    def _check_result(self, order_id):
        """
        Bloque jusqu'au résultat du trade. L'API renvoie (profit, statut) où
        statut vaut typiquement 'win' / 'loose' / 'equal' selon la version.
        """
        try:
            return self.api.CheckWin(order_id)
        except Exception as e:
            self.telegram.send_error(f"Erreur lecture résultat trade : {e}")
            return None, None

    def _run(self, signal: Signal):
        if signal.strategy == "5s":
            self._run_with_martingale(signal)
        else:
            self._run_single(signal)

    def _run_single(self, signal: Signal):
        amount = self.martingale_config.base_amount
        status, order_id = self._place_trade(signal.pair, signal.direction, amount, signal.expiration)
        if not status or order_id is None:
            return
        profit, result = self._check_result(order_id)
        won = str(result).lower() in ("win", "won", "true")
        self.telegram.send_trade_result(signal, amount, won, profit or 0.0)

    def _run_with_martingale(self, signal: Signal):
        martingale = self._get_martingale(signal.pair)

        while True:
            amount = martingale.next_amount()
            step = martingale.current_step
            status, order_id = self._place_trade(signal.pair, signal.direction, amount, signal.expiration)
            if not status or order_id is None:
                martingale.reset()
                return

            profit, result = self._check_result(order_id)
            won = str(result).lower() in ("win", "won", "true")

            self.telegram.send_trade_result(signal, amount, won, profit or 0.0, martingale_step=step)

            sequence_done = martingale.register_result(won)
            if sequence_done:
                if not won:
                    self.telegram.send_text(
                        f"⛔ Martingale {signal.pair} : {self.martingale_config.max_steps} étapes "
                        f"atteintes sans victoire. Retour à la mise de base."
                    )
                return
            # sinon : on reste dans la boucle, on rejoue immédiatement la même
            # direction avec la mise augmentée (comportement martingale classique)
