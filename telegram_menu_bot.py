"""
telegram_menu_bot.py
----------------------
Bot Telegram interactif (menu à boutons), inspiré des captures d'écran
fournies : mode Démo/Réel, "Lancer l'auto-trading" / "Mode manuel", choix
de la mise de départ, choix de l'actif (auto ou manuel), choix de la
stratégie, mode "Série de deals" (avec martingale pour la stratégie 5s),
messages de progression animés et log étape par étape.

⚠️ Le mode "Par take-profit" est volontairement VERROUILLÉ pour l'instant
(config.TAKE_PROFIT_MODE_ENABLED = False) : cliquer sur le bouton affiche
juste un message "bientôt disponible", comme demandé.

⚠️ Je n'ai pas reproduit les éléments publicitaires/de parrainage visibles
sur les captures d'un autre bot (pub, lien de parrainage, statistiques de
gain gonflées) : ce ne sont pas des fonctionnalités techniques, ce sont des
pratiques marketing trompeuses que je ne veux pas te construire.

Lancer ce fichier directement : python telegram_menu_bot.py
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import config
from telegram_api import TelegramAPI, keyboard
from strategies import strategy_5s, strategy_1m, strategy_2m
from strategies.martingale import MartingaleManager, MartingaleConfig
from pocketoptionapi.stable_api import PocketOption


STRATEGIES = {
    "5s": (strategy_5s, config.STRATEGY_5S_CONFIG, 5, True),    # (module, config, period_bougies, martingale?)
    "1M": (strategy_1m, config.STRATEGY_1M_CONFIG, 60, False),
    "2M": (strategy_2m, config.STRATEGY_2M_CONFIG, 120, False),
}


def get_signal_for(api, pair: str, strategy_name: str):
    module, cfg, period, _ = STRATEGIES[strategy_name]
    df = api.get_dataframe(pair, period)
    if df is None or len(df) < 30:
        return None
    df = df.sort_values("time").reset_index(drop=True)
    return module.analyze(df, pair, cfg)


def pick_best_pair(api, pairs, strategy_name: str):
    best_pair, best_conf = None, -1
    for pair in pairs:
        try:
            sig = get_signal_for(api, pair.strip(), strategy_name)
        except Exception:
            sig = None
        if sig and sig.confidence > best_conf:
            best_pair, best_conf = pair.strip(), sig.confidence
    return best_pair or pairs[0].strip()


def progress_bar(total: int, remaining: int, width: int = 15) -> str:
    done = int(width * (total - remaining) / max(total, 1))
    return "▬" * done + "▭" * (width - done)


@dataclass
class Session:
    demo: bool = True
    stake: float = 1.0
    pair: Optional[str] = None
    strategy: Optional[str] = None
    stop: bool = False
    running: bool = False


class MenuBot:
    def __init__(self, api: PocketOption, tg_token: str):
        self.api = api
        self.tg = TelegramAPI(tg_token)
        self.sessions = {}

    def _session(self, chat_id) -> Session:
        if chat_id not in self.sessions:
            self.sessions[chat_id] = Session(demo=config.POCKET_DEMO)
        return self.sessions[chat_id]

    # ------------------------------------------------------------------ Menus
    def show_main_menu(self, chat_id, message_id=None):
        s = self._session(chat_id)
        try:
            balance = self.api.GetBalance()
        except Exception:
            balance = "?"
        mode_txt = "DÉMO" if s.demo else "RÉEL"
        text = f"🟠 Mode : <b>{mode_txt}</b>\n💰 Solde : <b>{balance}</b>"
        kb = keyboard([
            [("🚀 Lancer l'auto-trading", "menu:start_auto")],
            [("🎮 Mode manuel", "menu:manual_mode")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_stake_menu(self, chat_id, message_id):
        s = self._session(chat_id)
        mode_txt = "DÉMO" if s.demo else "RÉEL"
        text = f"Choisissez votre mise de départ :\n\nLa session tournera sur : <b>{mode_txt}</b>"
        row = [(f"${a}", f"stake:{a}") for a in config.STAKE_OPTIONS]
        kb = keyboard([row, [("⬅ Menu", "menu:main")]])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_asset_menu(self, chat_id, message_id):
        text = (
            "🎯 Actif\n\nChoisir l'actif automatiquement (le bot prend la "
            "paire au meilleur signal) ou manuellement ?"
        )
        kb = keyboard([
            [("🎯 Sélection auto", "asset:auto")],
            [("🔧 Choisir manuellement", "asset:manual")],
            [("⬅ Menu", "menu:main")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_asset_list(self, chat_id, message_id):
        rows = [[(p.strip(), f"asset:pick:{p.strip()}")] for p in config.PAIRS]
        rows.append([("⬅ Menu", "menu:main")])
        self._send_or_edit(chat_id, message_id, "Choisis une paire :", keyboard(rows))

    def show_strategy_menu(self, chat_id, message_id):
        text = "📊 Quelle stratégie veux-tu lancer ?"
        kb = keyboard([
            [("⚡ 5s (martingale)", "strategy:5s")],
            [("🕐 1M", "strategy:1M")],
            [("🕑 2M", "strategy:2M")],
            [("⬅ Menu", "menu:main")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_autotype_menu(self, chat_id, message_id):
        s = self._session(chat_id)
        text = (
            "🤖 Mode auto-trading\n\n"
            f"🎯 <b>Série de {config.DEALS_PER_SERIES} deals</b> — {config.DEALS_PER_SERIES} deals d'affilée, "
            "chacun avec des étapes (martingale si la stratégie 5s est choisie). "
            "Un deal se ferme dès qu'il est en profit (ou après le nombre max d'étapes). "
            "Sans take-profit ni stop-loss.\n\n"
            "📈 <b>Par take-profit</b> — trade jusqu'à un profit défini (+$) ou un "
            "stop-loss (−$), sans limite de deals."
        )
        tp_label = "📈 Par take-profit" if config.TAKE_PROFIT_MODE_ENABLED else "🔒 📈 Par take-profit"
        kb = keyboard([
            [(f"🎯 Série ({config.DEALS_PER_SERIES} deals)", "autotype:series")],
            [(tp_label, "autotype:takeprofit")],
            [("⬅ Menu", "menu:main")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def _send_or_edit(self, chat_id, message_id, text, kb):
        if message_id:
            self.tg.edit_message(chat_id, message_id, text, kb)
        else:
            self.tg.send_message(chat_id, text, kb)

    # ---------------------------------------------------------------- Router
    def handle_update(self, update: dict):
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            if text in ("/start", "/menu"):
                self.show_main_menu(chat_id)
            return

        if "callback_query" not in update:
            return

        cq = update["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        message_id = cq["message"]["message_id"]
        data = cq.get("data", "")
        self.tg.answer_callback(cq["id"])

        s = self._session(chat_id)

        if data == "menu:main":
            self.show_main_menu(chat_id, message_id)

        elif data == "menu:start_auto":
            self.show_stake_menu(chat_id, message_id)

        elif data == "menu:manual_mode":
            config.STRATEGY_5S_MODE = "manual"
            config.STRATEGY_1M_MODE = "manual"
            config.STRATEGY_2M_MODE = "manual"
            self.tg.edit_message(
                chat_id, message_id,
                "🎮 Mode manuel activé.\nTu recevras les signaux ici — à toi de "
                "passer les trades toi-même sur PocketOption.",
                keyboard([[("⬅ Menu", "menu:main")]]),
            )

        elif data.startswith("stake:"):
            s.stake = float(data.split(":")[1])
            self.show_asset_menu(chat_id, message_id)

        elif data == "asset:auto":
            s.pair = None  # sera choisi automatiquement au lancement
            self.show_strategy_menu(chat_id, message_id)

        elif data == "asset:manual":
            self.show_asset_list(chat_id, message_id)

        elif data.startswith("asset:pick:"):
            s.pair = data.split(":", 2)[2]
            self.show_strategy_menu(chat_id, message_id)

        elif data.startswith("strategy:"):
            s.strategy = data.split(":")[1]
            self.show_autotype_menu(chat_id, message_id)

        elif data == "autotype:takeprofit":
            self.tg.edit_message(
                chat_id, message_id,
                "🔒 Le mode « Par take-profit » n'est pas encore disponible.\n"
                "Utilise « Série de deals » pour l'instant.",
                keyboard([[("⬅ Menu", "menu:main")]]),
            )

        elif data == "autotype:series":
            if s.pair is None:
                s.pair = pick_best_pair(self.api, config.PAIRS, s.strategy)
            s.stop = False
            s.running = True
            thread = threading.Thread(target=self._run_series, args=(chat_id, message_id, s), daemon=True)
            thread.start()

        elif data == "run:stop":
            s.stop = True
            self.tg.answer_callback(cq["id"], "Arrêt demandé...")

    # -------------------------------------------------------------- Exécution
    def _run_series(self, chat_id, message_id, s: Session):
        pair = s.pair
        strategy_name = s.strategy
        has_martingale = STRATEGIES[strategy_name][3]
        try:
            balance_start = self.api.GetBalance()
        except Exception:
            balance_start = 0.0

        header = (
            f"🤖 Auto-trading · {pair} ({'DÉMO' if s.demo else 'RÉEL'})\n"
            f"Stratégie : {strategy_name}"
        )
        log_lines = []
        pnl_session = 0.0
        stop_kb = keyboard([[("⏹ Arrêter", "run:stop")]])

        def render(extra=""):
            body = "\n".join(log_lines[-12:])  # on garde les 12 dernières lignes affichées
            return f"{header}\nP&amp;L session : {pnl_session:+.2f}\n\n{body}\n{extra}".strip()

        self.tg.edit_message(chat_id, message_id, render(), stop_kb)

        deals_total = config.DEALS_PER_SERIES
        for deal_idx in range(1, deals_total + 1):
            if s.stop:
                log_lines.append("⏹ Arrêté par l'utilisateur.")
                break

            martingale = (
                MartingaleManager(MartingaleConfig(base_amount=s.stake, multiplier=2.2, max_steps=5))
                if has_martingale else None
            )
            step = 1
            deal_pnl = 0.0
            deal_won = False
            max_steps = 1 if not has_martingale else 999  # boucle contrôlée par register_result

            while True:
                if s.stop:
                    break

                sig = self._wait_for_signal(pair, strategy_name, timeout=90)
                if sig is None:
                    log_lines.append(f"⚠️ Deal {deal_idx} : pas de signal dans le délai, deal ignoré")
                    self.tg.edit_message(chat_id, message_id, render(), stop_kb)
                    break

                amount = martingale.next_amount() if martingale else s.stake

                try:
                    status, order_id = self.api.Buy(amount, pair, sig.direction, sig.expiration)
                except Exception as e:
                    log_lines.append(f"⚠️ Erreur d'ouverture : {e}")
                    self.tg.edit_message(chat_id, message_id, render(), stop_kb)
                    break

                if not status or order_id is None:
                    log_lines.append(f"⚠️ Deal {deal_idx} : échec d'ouverture du trade")
                    self.tg.edit_message(chat_id, message_id, render(), stop_kb)
                    break

                # Animation pendant l'attente de l'expiration
                self._animate_wait(chat_id, message_id, render, sig.expiration, stop_kb)

                try:
                    profit, result = self.api.CheckWin(order_id)
                except Exception:
                    profit, result = None, None

                won = str(result).lower() in ("win", "won", "true")
                profit_val = profit if isinstance(profit, (int, float)) else (amount if won else -amount)
                deal_pnl += profit_val
                emoji = "🟢" if won else "🔴"
                log_lines.append(f"⚡ Deal {deal_idx}·Étape {step} | {emoji} ${amount:.2f} → {profit_val:+.2f}")
                self.tg.edit_message(chat_id, message_id, render(), stop_kb)

                if martingale:
                    done = martingale.register_result(won)
                    deal_won = won
                    if done:
                        break
                    step += 1
                else:
                    deal_won = won
                    break

            pnl_session += deal_pnl
            icon = "✅" if deal_won else "❌"
            log_lines.append(
                f"{icon} Deal {deal_idx}/{deals_total} clôturé en "
                f"{'profit' if deal_won else 'perte'} ({deal_pnl:+.2f})"
            )
            self.tg.edit_message(chat_id, message_id, render(), stop_kb)

        try:
            balance_end = self.api.GetBalance()
        except Exception:
            balance_end = balance_start

        log_lines.append(f"🏁 Terminé : {deal_idx}/{deals_total} deals")
        final_text = (
            f"{render()}\n\nSolde : {balance_start} → {balance_end}"
        )
        self.tg.edit_message(chat_id, message_id, final_text, keyboard([[("⬅ Menu", "menu:main")]]))
        s.running = False

    def _wait_for_signal(self, pair, strategy_name, timeout=90):
        elapsed = 0
        interval = max(1, config.SCAN_INTERVAL_SECONDS)
        while elapsed < timeout:
            try:
                sig = get_signal_for(self.api, pair, strategy_name)
            except Exception:
                sig = None
            if sig and sig.confidence >= config.MIN_CONFIDENCE_TO_AUTOTRADE:
                return sig
            time.sleep(interval)
            elapsed += interval
        return None

    def _animate_wait(self, chat_id, message_id, render_fn, seconds: int, stop_kb):
        step = 2 if seconds > 30 else 1
        remaining = seconds
        while remaining > 0:
            bar = progress_bar(seconds, remaining)
            self.tg.edit_message(
                chat_id, message_id,
                f"{render_fn()}\n\n⏱ trade en cours {bar} {remaining}s",
                stop_kb,
            )
            time.sleep(step)
            remaining -= step

    def run_forever(self):
        print("Menu Telegram démarré (long polling). Ctrl+C pour arrêter.")
        while True:
            for update in self.tg.get_updates():
                try:
                    self.handle_update(update)
                except Exception as e:
                    print(f"[MenuBot] Erreur sur update : {e}")


if __name__ == "__main__":
    api = PocketOption(demo=config.POCKET_DEMO, ssid=config.POCKET_SSID or None)
    api.connect()
    time.sleep(3)
    bot = MenuBot(api, config.TELEGRAM_BOT_TOKEN)
    bot.run_forever()
