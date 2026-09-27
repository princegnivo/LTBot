import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PO_SSID = os.getenv("PO_SSID")
PO_DEMO = os.getenv("PO_DEMO", "True").lower() == "true"

STRATEGY = {
    "rsi_period": 14,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "wick_percent": 30,  # "Pourcentage d'ombre" — 0 = filtre désactivé
    "trade_duration_sec": 5,
}

MONEY = {
    "base_stake": 1,
    "martingale_multiplier": 2,
    "max_steps": 4,
}
