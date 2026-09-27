"""
telegram_api.py
----------------
Petit wrapper HTTP autour de l'API Telegram Bot, utilisé par le menu
interactif (telegram_menu_bot.py). Pas de dépendance lourde type
python-telegram-bot : juste ce qu'il faut (getUpdates en long polling,
sendMessage, editMessageText, answerCallbackQuery).
"""

import requests


class TelegramAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._offset = 0

    def get_updates(self, timeout: int = 25):
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={"timeout": timeout, "offset": self._offset + 1},
                timeout=timeout + 10,
            )
            data = resp.json()
        except requests.RequestException as e:
            print(f"[TelegramAPI] Erreur getUpdates : {e}")
            return []
        if not data.get("ok"):
            return []
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"]
        return updates

    def send_message(self, chat_id, text: str, reply_markup: dict = None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
        try:
            return resp.json().get("result")
        except Exception:
            return None

    def edit_message(self, chat_id, message_id, text: str, reply_markup: dict = None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        resp = requests.post(f"{self.base_url}/editMessageText", json=payload, timeout=15)
        try:
            return resp.json().get("result")
        except Exception:
            return None

    def answer_callback(self, callback_query_id, text: str = None):
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        requests.post(f"{self.base_url}/answerCallbackQuery", json=payload, timeout=10)


def keyboard(rows):
    """
    rows : liste de lignes, chaque ligne = liste de tuples (label, callback_data).
    Retourne la structure inline_keyboard attendue par l'API Telegram.
    """
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in rows
        ]
    }
