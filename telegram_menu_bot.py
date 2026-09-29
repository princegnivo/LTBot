"""
telegram_menu_bot.py
----------------------
Bot Telegram interactif (menu à boutons), calqué sur la vidéo d'exemple
fournie : mode Démo/Réel, "Lancer l'auto-trading" / "Mode manuel", choix
de la mise de départ, choix de l'actif (auto = ALÉATOIRE parmi les
paires à 92%, ou manuel = liste des paires à 92% uniquement), choix de
la stratégie (5s / 1M / 2M), mode "Série de deals" (avec martingale,
2 relances MAXIMUM), messages de progression animés et log étape par
étape.

Corrections apportées suite à l'analyse des stratégies vidéo :
    - Seules les paires à 92% de payout sont proposées (auto ET manuel).
    - Le choix automatique de l'actif est ALÉATOIRE (pas "meilleure
      confiance").
    - Il n'y a plus AUCUN délai/timeout d'attente de signal : le bot
      attend une confirmation aussi longtemps qu'il le faut, il n'y a
      donc plus de message "pas de signal dans le délai" ni de deal
      clôturé sans trade.
    - En mode manuel, on choisit le délai (5s / 1M / 2M) avant de
      recevoir les signaux.
    - La martingale (2 relances MAXIMUM après une perte, jamais plus)
      s'applique à la stratégie en cours quelle qu'elle soit, puis le
      bot attend une toute nouvelle confirmation.
    - L'arrêt d'une série est toujours automatique en fin de séquence
      (et aussi en cas d'erreur), le bouton "Arrêter" reste disponible
      pour un arrêt anticipé manuel.

Le mode "Par take-profit" est volontairement VERROUILLÉ pour l'instant
(config.TAKE_PROFIT_MODE_ENABLED = False) : cliquer sur le bouton affiche
juste un message "bientôt disponible", comme demandé.

Je n'ai pas reproduit les éléments publicitaires/de parrainage visibles
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
import pairs as pairs_module
import candles as candles_module
from telegram_api import TelegramAPI, keyboard
from strategies import strategy_5s, strategy_1m, strategy_2m
from strategies.martingale import MartingaleManager, MartingaleConfig
from pocketoptionapi.stable_api import PocketOption


# (module, config, période bougies en secondes, nom affiché)
STRATEGIES = {
    "5s": (strategy_5s, config.STRATEGY_5S_CONFIG, 5, "5 secondes"),
    "1M": (strategy_1m, config.STRATEGY_1M_CONFIG, 60, "1 minute"),
    "2M": (strategy_2m, config.STRATEGY_2M_CONFIG, 120, "2 minutes"),
}

# Seule "5s" utilise la martingale (demandé explicitement).
STRATEGIES_WITH_MARTINGALE = {"5s"}

STRATEGY_MODE_ATTR = {
    "5s": "STRATEGY_5S_MODE",
    "1M": "STRATEGY_1M_MODE",
    "2M": "STRATEGY_2M_MODE",
}


def get_signal_for(api, pair: str, strategy_name: str):
    module, cfg, period, _ = STRATEGIES[strategy_name]
    # Correctif du bug racine : sans cet appel, l'API ne reçoit jamais
    # aucune bougie pour cette paire (voir candles.py pour le détail).
    candles_module.ensure_ready(api, pair, period)
    df = api.get_dataframe(pair, period)
    if df is None or len(df) < 30:
        return None
    df = df.sort_values("time").reset_index(drop=True)
    return module.analyze(df, pair, cfg)


def is_otc_pair(pair: str) -> bool:
    return pair.strip().lower().endswith("_otc")


def progress_bar(total: int, remaining: int, width: int = 15) -> str:
    done = int(width * (total - remaining) / max(total, 1))
    return "▬" * done + "▭" * (width - done)


@dataclass
class Session:
    demo: bool = True
    stake: float = 1.0
    pair: Optional[str] = None
    strategy: Optional[str] = None
    market: str = "otc"          # "otc" ou "real" (filtre en sélection manuelle d'actif)
    manual_delay: Optional[str] = None   # "5s" / "1M" / "2M" choisi en mode manuel
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
            f"🎯 Actif\n\nSeules les paires à {config.TARGET_PAYOUT}% de paiement "
            "sont proposées.\n\nChoisir l'actif automatiquement (le bot en tire une au "
            "hasard parmi les paires à 92%) ou manuellement ?"
        )
        kb = keyboard([
            [("🎲 Sélection auto (aléatoire)", "asset:auto")],
            [("🔧 Choisir manuellement", "asset:manual_market")],
            [("⬅ Menu", "menu:main")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_market_menu(self, chat_id, message_id, prefix="asset"):
        text = "Marché :"
        kb = keyboard([
            [("💠 OTC", f"{prefix}:market:otc"), ("🏦 Réel", f"{prefix}:market:real")],
            [("⬅ Menu", "menu:main")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_asset_list(self, chat_id, message_id):
        s = self._session(chat_id)
        eligible = pairs_module.get_eligible_pairs(self.api)
        filtered = [p for p in eligible if is_otc_pair(p) == (s.market == "otc")]
        if not filtered:
            filtered = eligible
        rows = [[(p, f"asset:pick:{p}")] for p in filtered[:20]]
        rows.append([("⬅ Menu", "menu:main")])
        text = f"Paires à {config.TARGET_PAYOUT}% ({'OTC' if s.market == 'otc' else 'Réel'}) :"
        self._send_or_edit(chat_id, message_id, text, keyboard(rows))

    def show_strategy_menu(self, chat_id, message_id):
        text = "📊 Quelle stratégie veux-tu lancer ?"
        kb = keyboard([
            [(f"⚡ {STRATEGIES['5s'][3]} (RSI + mèche)", "strategy:5s")],
            [(f"🕐 {STRATEGIES['1M'][3]} (MA5 + Williams %R)", "strategy:1M")],
            [(f"🕑 {STRATEGIES['2M'][3]} (Bollinger + MACD)", "strategy:2M")],
            [("⬅ Menu", "menu:main")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_autotype_menu(self, chat_id, message_id):
        text = (
            "🤖 Mode auto-trading\n\n"
            f"🎯 <b>Série de {config.DEALS_PER_SERIES} deals GAGNANTS</b> — le bot continue "
            f"jusqu'à obtenir {config.DEALS_PER_SERIES} deals gagnants. Une perte ne compte "
            "PAS comme un deal de la série : elle ne fait qu'avancer la martingale "
            "(2 relances MAXIMUM, uniquement sur la stratégie 5s ; 1M et 2M tradent "
            "en mise simple, sans relance). Si la perte persiste après la martingale "
            "(ou après un trade simple en 1M/2M), le bot attend une toute nouvelle "
            "confirmation et retente — la série n'avance que sur les gains. "
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

    def show_manual_delay_menu(self, chat_id, message_id):
        text = "🎮 Mode manuel\n\nChoisis le délai (expiration) sur lequel tu veux recevoir les signaux :"
        kb = keyboard([
            [(f"⚡ {STRATEGIES['5s'][3]}", "manual:delay:5s")],
            [(f"🕐 {STRATEGIES['1M'][3]}", "manual:delay:1M")],
            [(f"🕑 {STRATEGIES['2M'][3]}", "manual:delay:2M")],
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
            self.show_manual_delay_menu(chat_id, message_id)

        elif data.startswith("manual:delay:"):
            delay = data.split(":")[2]
            s.manual_delay = delay
            for name, attr in STRATEGY_MODE_ATTR.items():
                setattr(config, attr, "manual" if name == delay else "off")
            self.tg.edit_message(
                chat_id, message_id,
                f"🎮 Mode manuel activé — délai : <b>{STRATEGIES[delay][3]}</b>.\n"
                "Tu recevras ici les signaux dès que toutes les confirmations sont "
                "réunies — à toi de passer les trades toi-même sur PocketOption.",
                keyboard([[("Menu", "menu:main")]]),
            )

        elif data.startswith("stake:"):
            s.stake = float(data.split(":")[1])
            self.show_asset_menu(chat_id, message_id)

        elif data == "asset:auto":
            s.pair = None  # sera tiré au hasard au lancement
            self.show_strategy_menu(chat_id, message_id)

        elif data == "asset:manual_market":
            self.show_market_menu(chat_id, message_id, prefix="asset")

        elif data.startswith("asset:market:"):
            s.market = data.split(":")[2]
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
                keyboard([[("Menu", "menu:main")]]),
            )

        elif data == "autotype:series":
            if s.pair is None:
                s.pair = pairs_module.pick_random_pair(self.api)
            if s.pair is None:
                self.tg.edit_message(
                    chat_id, message_id,
                    f"⚠️ Aucune paire à {config.TARGET_PAYOUT}% de paiement disponible "
                    "actuellement. Réessaie dans un instant.",
                    keyboard([[("⬅ Menu", "menu:main")]]),
                )
                return
            s.stop = False
            s.running = True
            thread = threading.Thread(target=self._run_series, args=(chat_id, message_id, s), daemon=True)
            thread.start()

        elif data == "run:stop":
            s.stop = True
            self.tg.answer_callback(cq["id"], "Arrêt demandé...")

    # -------------------------------------------------------------- Exécution
    def _run_series(self, chat_id, message_id, s: Session):
        """
        Série de N deals GAGNANTS (config.DEALS_PER_SERIES) :
            - Une perte ne compte PAS comme un deal de la série.
            - Sur "5s" : martingale 2 relances max avant de considérer le
              deal comme perdu (série non avancée, nouvelle confirmation
              attendue ensuite).
            - Sur "1M"/"2M" : mise simple, pas de relance ; une perte fait
              juste attendre une nouvelle confirmation, sans faire avancer
              la série.
            - La série se termine automatiquement dès que le nombre de
              GAINS demandé est atteint (ou sur arrêt manuel / erreur).
        """
        pair = s.pair
        strategy_name = s.strategy
        has_martingale = strategy_name in STRATEGIES_WITH_MARTINGALE
        try:
            balance_start = self.api.GetBalance()
        except Exception:
            balance_start = 0.0

        payout_txt = f"{config.TARGET_PAYOUT}%"
        header = (
            f"🤖 Auto-trading · {pair} ({'DÉMO' if s.demo else 'RÉEL'}) · paiement {payout_txt}\n"
            f"Stratégie : {strategy_name}"
        )
        log_lines = []
        pnl_session = 0.0
        stop_kb = keyboard([[("⏹ Arrêter", "run:stop")]])
        deals_target = config.DEALS_PER_SERIES  # nombre de GAINS visés
        wins = 0
        attempt_idx = 0

        def render(extra=""):
            body = "\n".join(log_lines[-12:])  # on garde les 12 dernières lignes affichées
            return (
                f"{header}\n"
                f"Gagnés : {wins}/{deals_target} · P&amp;L session : {pnl_session:+.2f}\n\n"
                f"{body}\n{extra}"
            ).strip()

        self.tg.edit_message(chat_id, message_id, render(), stop_kb)

        try:
            while wins < deals_target:
                if s.stop:
                    log_lines.append("⏹ Arrêté par l'utilisateur.")
                    break

                attempt_idx += 1

                # Aucun délai/timeout : on attend aussi longtemps qu'il le
                # faut qu'une confirmation complète apparaisse.
                sig = self._wait_for_signal(pair, strategy_name, s)
                if sig is None:
                    # Uniquement possible si l'utilisateur a arrêté la série.
                    break

                if has_martingale:
                    deal_pnl, deal_won = self._execute_martingale_deal(
                        chat_id, message_id, render, stop_kb, s, sig, attempt_idx, log_lines
                    )
                else:
                    deal_pnl, deal_won = self._execute_single_deal(
                        chat_id, message_id, render, stop_kb, s, sig, attempt_idx, log_lines
                    )

                if deal_pnl is None:
                    # Erreur d'ouverture / échec technique : on continue,
                    # on attendra une nouvelle confirmation.
                    if s.stop:
                        break
                    continue

                pnl_session += deal_pnl

                if deal_won:
                    wins += 1
                    log_lines.append(f"✅ Deal gagnant {wins}/{deals_target} ({deal_pnl:+.2f})")
                else:
                    log_lines.append(
                        f"❌ Perte ({deal_pnl:+.2f}) — ne compte pas dans les {deals_target} "
                        "gagnants, attente d'une nouvelle confirmation."
                    )
                self.tg.edit_message(chat_id, message_id, render(), stop_kb)

                if s.stop:
                    break
        except Exception as e:
            # Arrêt automatique même en cas d'erreur imprévue, comme demandé.
            log_lines.append(f"⚠️ Erreur inattendue, arrêt automatique de la série : {e}")

        try:
            balance_end = self.api.GetBalance()
        except Exception:
            balance_end = balance_start

        if wins >= deals_target:
            log_lines.append(f"🏁 Terminé : {wins}/{deals_target} deals gagnants atteints — arrêt automatique.")
        else:
            log_lines.append(f"🏁 Arrêt : {wins}/{deals_target} deals gagnants.")
        final_text = f"{render()}\n\nSolde : {balance_start} → {balance_end}"
        # L'arrêt est toujours automatique en sortie de boucle : plus besoin
        # de cliquer "Arrêter" pour que la session se termine proprement.
        self.tg.edit_message(chat_id, message_id, final_text, keyboard([[("⬅ Menu", "menu:main")]]))
        s.running = False
        s.stop = False

    def _execute_single_deal(self, chat_id, message_id, render, stop_kb, s: Session, sig, attempt_idx, log_lines):
        """1M / 2M : un seul trade, pas de martingale. Renvoie (pnl, won) ou (None, False) si échec technique."""
        amount = s.stake
        try:
            status, order_id = self.api.Buy(amount, sig.pair, sig.direction, sig.expiration)
        except Exception as e:
            log_lines.append(f"⚠️ Erreur d'ouverture : {e}")
            self.tg.edit_message(chat_id, message_id, render(), stop_kb)
            return None, False

        if not status or order_id is None:
            log_lines.append(f"⚠️ Confirmation {attempt_idx} : échec d'ouverture du trade")
            self.tg.edit_message(chat_id, message_id, render(), stop_kb)
            return None, False

        self._animate_wait(chat_id, message_id, render, sig.expiration, stop_kb, s)

        try:
            profit, result = self.api.CheckWin(order_id)
        except Exception:
            profit, result = None, None

        won = str(result).lower() in ("win", "won", "true")
        profit_val = profit if isinstance(profit, (int, float)) else (amount if won else -amount)
        emoji = "🟢" if won else "🔴"
        log_lines.append(f"⚡ Confirmation {attempt_idx} | {emoji} ${amount:.2f} → {profit_val:+.2f}")
        self.tg.edit_message(chat_id, message_id, render(), stop_kb)
        return profit_val, won

    def _execute_martingale_deal(self, chat_id, message_id, render, stop_kb, s: Session, sig, attempt_idx, log_lines):
        """5s uniquement : martingale 2 relances MAXIMUM après une perte. Renvoie (pnl_total, won_final)."""
        martingale = MartingaleManager(
            MartingaleConfig(base_amount=s.stake, multiplier=2.2, max_steps=config.MARTINGALE_CONFIG.max_steps)
        )
        step = 1
        deal_pnl = 0.0
        deal_won = False
        direction = sig.direction
        pair = sig.pair
        expiration = sig.expiration

        while True:
            if s.stop:
                break

            amount = martingale.next_amount()
            try:
                status, order_id = self.api.Buy(amount, pair, direction, expiration)
            except Exception as e:
                log_lines.append(f"⚠️ Erreur d'ouverture : {e}")
                self.tg.edit_message(chat_id, message_id, render(), stop_kb)
                return (deal_pnl if step > 1 else None), False

            if not status or order_id is None:
                log_lines.append(f"⚠️ Confirmation {attempt_idx} : échec d'ouverture du trade")
                self.tg.edit_message(chat_id, message_id, render(), stop_kb)
                return (deal_pnl if step > 1 else None), False

            self._animate_wait(chat_id, message_id, render, expiration, stop_kb, s)

            try:
                profit, result = self.api.CheckWin(order_id)
            except Exception:
                profit, result = None, None

            won = str(result).lower() in ("win", "won", "true")
            profit_val = profit if isinstance(profit, (int, float)) else (amount if won else -amount)
            deal_pnl += profit_val
            emoji = "🟢" if won else "🔴"
            step_txt = f"Étape {step}" if step > 1 else "Mise initiale"
            log_lines.append(f"⚡ Confirmation {attempt_idx}·{step_txt} | {emoji} ${amount:.2f} → {profit_val:+.2f}")
            self.tg.edit_message(chat_id, message_id, render(), stop_kb)

            deal_won = won
            sequence_done = martingale.register_result(won)
            if sequence_done:
                if not won:
                    log_lines.append(
                        f"⛔ Martingale : {config.MARTINGALE_CONFIG.max_steps} relance(s) sans "
                        "victoire. Retour à la mise de base."
                    )
                break
            step += 1
            # sinon : on reste dans la boucle, on rejoue immédiatement
            # la même direction avec la mise augmentée (martingale)

        return deal_pnl, deal_won
    def _wait_for_signal(self, pair, strategy_name, s: Session):
        """
        Attend une confirmation complète de la stratégie, SANS DÉLAI
        MAXIMUM : tant que l'utilisateur n'a pas cliqué "Arrêter", on
        continue de scanner. Ne renvoie None que si l'utilisateur a
        demandé l'arrêt pendant l'attente.
        """
        interval = max(1, config.SCAN_INTERVAL_SECONDS)
        while not s.stop:
            try:
                sig = get_signal_for(self.api, pair, strategy_name)
            except Exception:
                sig = None
            if sig:
                return sig
            time.sleep(interval)
        return None

    def _animate_wait(self, chat_id, message_id, render_fn, seconds: int, stop_kb, s: Session):
        step = 2 if seconds > 30 else 1
        remaining = seconds
        while remaining > 0:
            if s.stop:
                break
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
