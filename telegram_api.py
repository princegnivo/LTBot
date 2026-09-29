"""
telegram_api.py
----------------
Petit wrapper HTTP autour de l'API Telegram Bot, utilisé par le menu
interactif (telegram_menu_bot.py). Pas de dépendance lourde type
python-telegram-bot : juste ce qu'il faut (getUpdates en long polling,
sendMessage, editMessageText, answerCallbackQuery).

Pourquoi cette version (correctif de la lenteur des boutons)
-------------------------------------------------------------
Avant, CHAQUE appel (`requests.post(...)`) ouvrait une toute nouvelle
connexion TCP + TLS vers api.telegram.org. Sur macOS, si l'IPv6 est
cassé/lent chez ton FAI ou si un proxy système est auto-détecté, chaque
nouvelle connexion perd plusieurs secondes AVANT d'envoyer la requête.
Un clic = getUpdates + answerCallbackQuery + editMessageText = 3 connexions
lentes => la réponse arrive après le délai de validité du clic et Telegram
répond "query is too old and response timeout expired".

Maintenant :
    - connexions PERSISTANTES (requests.Session, keep-alive) : la poignée
      de main TCP/TLS n'est payée qu'une fois ;
    - IPv4 forcé par défaut (TELEGRAM_FORCE_IPV4=false pour désactiver) ;
    - timeout de connexion court (5 s) + 2 retries automatiques ;
    - les mises à jour en attente d'un ancien lancement sont ignorées au
      démarrage (skip_pending_updates) : ce sont d'anciens clics déjà
      périmés qui provoquaient eux aussi l'erreur "too old" ;
    - tout appel > 2 s est signalé dans le terminal avec sa durée exacte,
      ce qui permet de voir immédiatement où est le goulot.
"""

import os
import socket
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection as _HTTPConnection
from urllib3.util import connection as _urllib3_connection
from urllib3.util.retry import Retry

SLOW_CALL_SECONDS = 2.0
CONNECT_TIMEOUT = 5
# Une connexion inactive depuis plus de N secondes est jetée avant réutilisation :
# les box/NAT/Wi-Fi la coupent en silence et la requête suivante resterait
# bloquée jusqu'au timeout (cause des clics à 7-30 s observés).
IDLE_RECYCLE_SECONDS = 4
POLL_TIMEOUT = 10  # long polling court : une connexion morte est détectée en <= ~15 s


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


if _env_bool("TELEGRAM_FORCE_IPV4", True):
    # urllib3 essaie les adresses une par une SANS "Happy Eyeballs" : si l'IPv6
    # est cassé, on attend tout le timeout IPv6 avant de tomber sur l'IPv4.
    _urllib3_connection.allowed_gai_family = lambda: socket.AF_INET


class _KeepAliveAdapter(HTTPAdapter):
    """Adapter avec TCP keep-alive, pour que la connexion du long polling ne soit
    pas tuée en silence par une box/NAT après quelques secondes d'inactivité."""

    def init_poolmanager(self, *args, **kwargs):
        opts = list(_HTTPConnection.default_socket_options)
        opts.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
        if hasattr(socket, "TCP_KEEPIDLE"):            # Linux
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10))
        elif hasattr(socket, "TCP_KEEPALIVE"):         # macOS
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 10))
        if hasattr(socket, "TCP_KEEPINTVL"):
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5))
        if hasattr(socket, "TCP_KEEPCNT"):
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3))
        kwargs["socket_options"] = opts
        super().init_poolmanager(*args, **kwargs)


def _make_session(pool_size: int = 10) -> requests.Session:
    s = requests.Session()
    # TELEGRAM_TRUST_ENV=false : ignore les proxys système (auto-détection
    # parfois très lente sous macOS).
    s.trust_env = _env_bool("TELEGRAM_TRUST_ENV", True)
    retry = Retry(total=2, connect=2, read=0, status=0, backoff_factor=0.3, allowed_methods=None)
    adapter = _KeepAliveAdapter(max_retries=retry, pool_connections=2, pool_maxsize=pool_size)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


class TelegramAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._offset = 0
        self._consecutive_timeouts = 0
        self.unauthorized = False
        # Deux sessions : une dédiée au long polling (connexion qui reste
        # ouverte), une pour les envois (utilisée par plusieurs threads).
        self._poll_session = _make_session(pool_size=1)
        self._send_session = _make_session(pool_size=10)
        self._send_last_used = 0.0

    def _safe(self, e) -> str:
        """Texte d'erreur SANS le token (les exceptions requests contiennent l'URL)."""
        return str(e).replace(self.token, "<TOKEN>")

    def _fresh_send_session(self):
        """Session d'envoi, avec connexions recyclées si elle est restée inactive."""
        now = time.time()
        if now - self._send_last_used > IDLE_RECYCLE_SECONDS:
            self._send_session.close()   # vide le pool ; la session reste utilisable
        self._send_last_used = now
        return self._send_session

    def _reset_poll_session(self):
        try:
            self._poll_session.close()
        except Exception:
            pass

    # ------------------------------------------------------------ Démarrage
    def check_connectivity(self) -> bool:
        """getMe : vérifie le token ET la joignabilité, en mesurant la latence."""
        try:
            t0 = time.time()
            resp = self._send_session.get(f"{self.base_url}/getMe", timeout=(CONNECT_TIMEOUT, 10))
            first = time.time() - t0
            data = resp.json()
            if data.get("ok"):
                bot_name = data.get("result", {}).get("username", "?")
                t1 = time.time()
                self._send_session.get(f"{self.base_url}/getMe", timeout=(CONNECT_TIMEOUT, 10))
                second = time.time() - t1
                print(
                    f"[TelegramAPI] Connexion OK — bot @{bot_name} "
                    f"(1er appel {first:.2f}s, appel suivant {second:.2f}s)"
                )
                if second > 1.5:
                    print(
                        "[TelegramAPI] ⚠️ Même avec connexion réutilisée, un appel prend "
                        f"{second:.1f}s : le réseau vers api.telegram.org est lent "
                        "(VPN ? Wi-Fi ? FAI ?). Lance `python3 diag_telegram.py`."
                    )
                return True
            if data.get("error_code") == 401:
                self.unauthorized = True
                print(
                    "[TelegramAPI] ❌ TOKEN REFUSÉ (401 Unauthorized). Le TELEGRAM_BOT_TOKEN lu par le bot "
                    "est invalide ou révoqué. Vérifie dans .env : une seule ligne "
                    "TELEGRAM_BOT_TOKEN=123456789:AAH... (sans guillemets ni espaces), fichier "
                    "sauvegardé, et pas de variable d'environnement du même nom dans ce terminal "
                    "(`unset TELEGRAM_BOT_TOKEN`). Token lu : "
                    f"{len(self.token)} caractères, se termine par ...{self.token[-4:]}"
                )
            else:
                print(f"[TelegramAPI] Réponse inattendue de getMe : {data}")
            return False
        except requests.RequestException as e:
            print(
                "[TelegramAPI] ⚠️ Impossible de joindre api.telegram.org "
                f"({self._safe(e)}). Vérifie ta connexion réseau (VPN/pare-feu/FAI "
                "qui bride Telegram ?) avec par ex. : "
                "curl -v https://api.telegram.org"
            )
            return False

    def skip_pending_updates(self):
        """
        Ignore tous les updates arrivés pendant que le bot était éteint.
        Ce sont d'anciens clics de boutons déjà périmés : les traiter ne sert
        à rien et Telegram les refuse avec "query is too old".
        """
        try:
            resp = self._poll_session.get(
                f"{self.base_url}/getUpdates",
                params={"offset": -1, "timeout": 0},
                timeout=(CONNECT_TIMEOUT, 10),
            )
            result = resp.json().get("result", [])
            if result:
                self._offset = result[-1]["update_id"]
                print("[TelegramAPI] Anciens messages/clics en attente ignorés.")
        except Exception as e:
            print(f"[TelegramAPI] skip_pending_updates : {self._safe(e)}")

    # -------------------------------------------------------------- Polling
    def get_updates(self, timeout: int = POLL_TIMEOUT):
        try:
            resp = self._poll_session.get(
                f"{self.base_url}/getUpdates",
                params={
                    "timeout": timeout,
                    "offset": self._offset + 1,
                    "allowed_updates": '["message","callback_query"]',
                },
                timeout=(CONNECT_TIMEOUT, timeout + 10),
            )
            data = resp.json()
            self._consecutive_timeouts = 0
        except requests.exceptions.ReadTimeout:
            self._reset_poll_session()  # connexion probablement morte : on repart à neuf
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts in (3, 10) or self._consecutive_timeouts % 30 == 0:
                print(
                    f"[TelegramAPI] {self._consecutive_timeouts} timeouts consécutifs sur "
                    "getUpdates — si ça persiste, le réseau vers api.telegram.org est "
                    "probablement bloqué/filtré (VPN, pare-feu, FAI)."
                )
            return []
        except requests.RequestException as e:
            self._reset_poll_session()
            self._consecutive_timeouts += 1
            print(f"[TelegramAPI] Erreur getUpdates (connexion réinitialisée) : {type(e).__name__}")
            time.sleep(min(0.5 * self._consecutive_timeouts, 5))  # backoff progressif
            return []
        except ValueError:
            time.sleep(1)
            return []
        if not data.get("ok"):
            time.sleep(1)
            return []
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"]
        return updates

    # ---------------------------------------------------------------- Envoi
    def _post(self, method: str, payload: dict, timeout: int = 15):
        """
        POST générique vers l'API Telegram qui NE FAIT JAMAIS ÉCHOUER
        SILENCIEUSEMENT : toute erreur réseau OU tout refus de l'API est
        imprimée avec la description exacte renvoyée par Telegram.
        Signale aussi tout appel lent (> SLOW_CALL_SECONDS).
        """
        t0 = time.time()
        attempts = 2 if method == "editMessageText" else 1   # édition = idempotente
        resp = None
        for attempt in range(attempts):
            try:
                resp = self._fresh_send_session().post(
                    f"{self.base_url}/{method}", json=payload, timeout=(CONNECT_TIMEOUT, timeout)
                )
                break
            except requests.RequestException as e:
                self._send_session.close()  # connexion suspecte : on repart à neuf
                if attempt + 1 >= attempts:
                    print(
                        f"[TelegramAPI] Erreur réseau sur {method} "
                        f"({time.time() - t0:.1f}s) : {type(e).__name__} — {self._safe(e)[:160]}"
                    )
                    return None
        elapsed = time.time() - t0
        if elapsed > SLOW_CALL_SECONDS:
            print(f"[TelegramAPI] ⏱ {method} lent : {elapsed:.1f}s (réseau vers Telegram ?)")
        try:
            data = resp.json()
        except Exception:
            print(f"[TelegramAPI] Réponse non-JSON sur {method} (HTTP {resp.status_code}) : {resp.text[:300]}")
            return None
        if not data.get("ok"):
            desc = data.get("description")
            # "message is not modified" = simple doublon d'édition, sans importance.
            if "message is not modified" not in str(desc):
                print(f"[TelegramAPI] {method} refusé par Telegram (HTTP {resp.status_code}) : {desc}")
            return None
        return data.get("result")

    def send_message(self, chat_id, text: str, reply_markup: dict = None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._post("sendMessage", payload)

    def edit_message(self, chat_id, message_id, text: str, reply_markup: dict = None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._post("editMessageText", payload)

    def answer_callback(self, callback_query_id, text: str = None):
        """À appeler UNE SEULE FOIS par clic (une 2e réponse est toujours refusée)."""
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self._post("answerCallbackQuery", payload, timeout=5)


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
