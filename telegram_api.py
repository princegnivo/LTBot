"""
telegram_api.py
----------------
Petit wrapper HTTP autour de l'API Telegram Bot, utilisé par le menu
interactif (telegram_menu_bot.py). Pas de dépendance lourde type
python-telegram-bot : juste ce qu'il faut (getUpdates en long polling,
sendMessage, editMessageText, answerCallbackQuery).

Robustesse : un "Read timed out" sur getUpdates est ATTENDU de temps en
temps en long polling (rien de nouveau pendant `timeout` secondes) et
n'est pas une erreur fatale — le code retente automatiquement. En
revanche, s'il se répète en boucle SUR CHAQUE appel, ce n'est plus un
comportement normal de long polling mais le signe que le réseau vers
api.telegram.org est bloqué/filtré/très instable (VPN, pare-feu, FAI qui
bride Telegram...). Voir check_connectivity() pour un diagnostic rapide
au démarrage.
"""

import time
import requests


class TelegramAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._offset = 0
        self._consecutive_timeouts = 0

    def check_connectivity(self) -> bool:
        """
        Appel bloquant COURT (getMe) pour vérifier au démarrage que le
        token est valide ET que api.telegram.org est bien joignable
        depuis ce réseau, AVANT d'entrer dans la boucle de long polling.
        """
        try:
            resp = requests.get(f"{self.base_url}/getMe", timeout=10)
            data = resp.json()
            if data.get("ok"):
                bot_name = data.get("result", {}).get("username", "?")
                print(f"[TelegramAPI] Connexion OK — bot @{bot_name}")
                return True
            print(f"[TelegramAPI] Réponse inattendue de getMe : {data}")
            return False
        except requests.RequestException as e:
            print(
                "[TelegramAPI] ⚠️ Impossible de joindre api.telegram.org "
                f"({e}). Vérifie ta connexion réseau (VPN/pare-feu/FAI "
                "qui bride Telegram ?) avec par ex. : "
                "curl -v https://api.telegram.org"
            )
            return False

    def get_updates(self, timeout: int = 20):
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={"timeout": timeout, "offset": self._offset + 1},
                timeout=timeout + 15,
            )
            data = resp.json()
            self._consecutive_timeouts = 0
        except requests.exceptions.ReadTimeout:
            # Normal de temps en temps en long polling (pas de nouvel
            # update pendant `timeout`s). On ne log que si ça se répète,
            # pour ne pas noyer la console d'un message anodin.
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts in (3, 10) or self._consecutive_timeouts % 30 == 0:
                print(
                    f"[TelegramAPI] {self._consecutive_timeouts} timeouts consécutifs sur "
                    "getUpdates — si ça persiste, le réseau vers api.telegram.org est "
                    "probablement bloqué/filtré (VPN, pare-feu, FAI)."
                )
            return []
        except requests.RequestException as e:
            self._consecutive_timeouts += 1
            print(f"[TelegramAPI] Erreur getUpdates : {e}")
            time.sleep(min(2 + self._consecutive_timeouts, 15))  # backoff progressif
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
