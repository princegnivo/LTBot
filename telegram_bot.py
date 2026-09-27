"""
telegram_bot.py
----------------
Client Telegram minimal basé sur l'API HTTP officielle (pas de dépendance
lourde type python-telegram-bot nécessaire juste pour envoyer des messages).

Utilise TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID définis dans config.py / .env.
"""

import requests

import config
from strategies.signal import Signal


class TelegramNotifier:
    def __init__(self, token: str = None, chat_id: str = None):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def _post(self, method: str, payload: dict):
        if not self.token or not self.chat_id:
            print("[Telegram] Token ou chat_id manquant, message non envoyé.")
            return None
        try:
            resp = requests.post(f"{self.base_url}/{method}", json=payload, timeout=10)
            if resp.status_code != 200:
                print(f"[Telegram] Erreur {resp.status_code} : {resp.text}")
            return resp
        except requests.RequestException as e:
            print(f"[Telegram] Erreur réseau : {e}")
            return None

    def send_text(self, text: str):
        return self._post(
            "sendMessage",
            {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
        )

    def send_signal(self, signal: Signal):
        return self.send_text(signal.to_telegram_text())

    def send_trade_result(self, signal: Signal, amount: float, won: bool, profit: float, martingale_step: int = 0):
        status = "✅ GAGNÉ" if won else "❌ PERDU"
        step_txt = f" (martingale étape {martingale_step})" if martingale_step else ""
        text = (
            f"{status}{step_txt} — {signal.pair} {signal.direction.upper()} [{signal.strategy}]\n"
            f"Mise : {amount:.2f} | Résultat : {profit:+.2f}"
        )
        return self.send_text(text)

    def send_error(self, message: str):
        return self.send_text(f"⚠️ Erreur bot : {message}")
