"""
config.py
---------
Configuration centrale du bot. Toutes les valeurs sensibles se chargent
depuis les variables d'environnement (ou un fichier .env via python-dotenv)
pour ne jamais committer de token/SSID en clair dans le code.

Copie .env.example en .env et remplis tes valeurs avant de lancer le bot.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv est optionnel ; on peut aussi juste exporter les variables d'env

from strategies.strategy_5s import Strategy5sConfig
from strategies.strategy_1m import Strategy1mConfig
from strategies.strategy_2m import Strategy2mConfig
from strategies.martingale import MartingaleConfig


# ---------------------------------------------------------------------------
# Compte PocketOption
# ---------------------------------------------------------------------------
POCKET_SSID = os.getenv("POCKET_SSID", "")          # optionnel : login auto via navigateur sinon
POCKET_DEMO = os.getenv("POCKET_DEMO", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Paires suivies et fréquence de scan
# ---------------------------------------------------------------------------
PAIRS = os.getenv("POCKET_PAIRS", "EURUSD_otc,GBPUSD_otc,AUDCAD_otc").split(",")
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "5"))

# ---------------------------------------------------------------------------
# Modes actifs au démarrage — un mode par stratégie
#   - "off"    : stratégie désactivée (ni notif, ni trade)
#   - "manual" : les signaux partent uniquement sur Telegram, aucun trade posé
#   - "auto"   : les signaux déclenchent un trade automatique (+ notif Telegram)
#   - "both"   : trade auto ET notif Telegram (comportement demandé par défaut)
# ---------------------------------------------------------------------------
STRATEGY_5S_MODE = os.getenv("STRATEGY_5S_MODE", "both")
STRATEGY_1M_MODE = os.getenv("STRATEGY_1M_MODE", "both")
STRATEGY_2M_MODE = os.getenv("STRATEGY_2M_MODE", "both")

# ---------------------------------------------------------------------------
# Paramètres des stratégies (modifiables ici sans toucher au reste du code)
# ---------------------------------------------------------------------------
STRATEGY_5S_CONFIG = Strategy5sConfig(
    expiration=5,
    rsi_period=14,
    rsi_oversold=30.0,
    rsi_overbought=70.0,
    shadow_percentage=30.0,   # 0 pour désactiver le filtre de mèche
)

STRATEGY_1M_CONFIG = Strategy1mConfig(
    expiration=60,
    ma_period=5,
    wr_period=14,
    wr_overbought=-20.0,
    wr_oversold=-80.0,
)

STRATEGY_2M_CONFIG = Strategy2mConfig(
    expiration=120,
    bb_period=6,
    bb_stddev=1.3,
    macd_fast=6,
    macd_slow=19,
    macd_signal=6,
)

# La martingale ne s'applique QU'À LA STRATÉGIE "5s" (comme demandé).
# "1M" et "2M" tradent en mise simple : pas de relance après une perte.
MARTINGALE_CONFIG = MartingaleConfig(
    base_amount=float(os.getenv("MARTINGALE_BASE_AMOUNT", "1.0")),
    multiplier=2.2,
    max_steps=2,
)

# ---------------------------------------------------------------------------
# Seuil minimum de confiance pour la NOTIFICATION Telegram uniquement
# (texte informatif). Il ne bloque plus le trade : dès que toutes les
# confirmations d'une stratégie sont réunies (analyze() renvoie un
# Signal), le trade est déclenché — comme demandé ("quand ces
# confirmations se réunissent, que ça marche").
# ---------------------------------------------------------------------------
MIN_CONFIDENCE_TO_NOTIFY = int(os.getenv("MIN_CONFIDENCE_TO_NOTIFY", "60"))

# ---------------------------------------------------------------------------
# Payout : on ne propose et ne trade QUE les paires à 92% (demandé).
# ---------------------------------------------------------------------------
TARGET_PAYOUT = int(os.getenv("TARGET_PAYOUT", "92"))

# ---------------------------------------------------------------------------
# Menu Telegram interactif (voir telegram_menu_bot.py)
# ---------------------------------------------------------------------------
STAKE_OPTIONS = [1, 2, 5, 10]              # boutons de mise de départ ($)
DEALS_PER_SERIES = 5                          # nombre de deals dans le mode "Série"
TAKE_PROFIT_MODE_ENABLED = False                 # verrouillé pour l'instant (à activer plus tard)
