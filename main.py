"""
main.py
--------
Point d'entrée du bot PocketOption.

Lance :
    1. La connexion à l'API PocketOption (démo ou réel selon .env)
    2. Le moteur de scan des paires avec les stratégies 5s et 2M
    3. La notification Telegram et/ou le trading automatique selon le mode
       configuré pour chaque stratégie (config.STRATEGY_5S_MODE / STRATEGY_2M_MODE)

Usage :
    python main.py
"""

import time

import config
from pocketoptionapi.stable_api import PocketOption
from telegram_bot import TelegramNotifier
from auto_trader import AutoTrader
from signal_engine import SignalEngine


def build_api() -> PocketOption:
    if config.POCKET_SSID:
        api = PocketOption(demo=config.POCKET_DEMO, ssid=config.POCKET_SSID)
    else:
        # Sans SSID fourni : ouverture d'une fenêtre de login PocketOption
        # (pywebview) pour récupérer automatiquement la session, comme dans
        # le projet d'origine.
        api = PocketOption(demo=config.POCKET_DEMO)
    api.connect()
    return api


def main():
    print(f"Démarrage du bot — compte {'DEMO' if config.POCKET_DEMO else 'RÉEL'}")
    print(f"Paires suivies : {config.PAIRS}")
    print(f"Mode stratégie 5s : {config.STRATEGY_5S_MODE} | Mode stratégie 2M : {config.STRATEGY_2M_MODE}")

    api = build_api()
    time.sleep(3)  # laisser le temps à la connexion WebSocket de s'établir

    balance = api.GetBalance()
    print(f"Solde du compte : {balance}")

    telegram = TelegramNotifier()
    telegram.send_text(
        f"🤖 Bot démarré — compte {'DEMO' if config.POCKET_DEMO else 'RÉEL'} — solde : {balance}"
    )

    auto_trader = AutoTrader(api, telegram, config.MARTINGALE_CONFIG)
    engine = SignalEngine(api, telegram, auto_trader)

    try:
        engine.run_forever()
    except KeyboardInterrupt:
        print("Arrêt demandé par l'utilisateur.")
        telegram.send_text("🛑 Bot arrêté.")


if __name__ == "__main__":
    main()
