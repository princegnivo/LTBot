# PocketOption Bot — Signaux Telegram + Trading Automatique

Bot Python basé sur l'API non officielle PocketOption (dossier `pocketoptionapi/`,
identique au projet d'origine), avec 3 stratégies personnalisées ("5s" avec
martingale, "1M" et "2M"), et deux façons de l'utiliser :

1. **Mode simple** (`main.py`) : scan en boucle + notifications Telegram + trading auto silencieux
2. **Menu interactif Telegram** (`telegram_menu_bot.py`) : boutons, choix de mise/actif/stratégie, mode "Série de deals" avec log animé étape par étape

## 📁 Structure

```
PocketOptionBot/
├── pocketoptionapi/            # Client API PocketOption (WebSocket, auth, candles...)
├── strategies/
│   ├── signal.py                 # Structure commune d'un signal
│   ├── strategy_5s.py              # RSI14 + mèche de confirmation, bougies 5s normales, martingale
│   ├── strategy_1m.py                # Bollinger20/2 + SMA2xSMA5 + RSI8, Heikin Ashi 60s
│   ├── strategy_2m.py                  # MACD(6,19,6) + Bollinger6/1.3, Heikin Ashi 120s
│   └── martingale.py                     # Gestion de la martingale (x2.2, 5 étapes)
├── indicators.py                 # Heikin Ashi, Bollinger, SMA, RSI, MACD (pandas pur)
├── telegram_bot.py                 # Notifs simples (mode "main.py")
├── telegram_api.py                   # Wrapper HTTP bas niveau (getUpdates, editMessageText...)
├── telegram_menu_bot.py                # Menu interactif à boutons (mode recommandé)
├── auto_trader.py                        # Exécution des trades auto + martingale (mode "main.py")
├── signal_engine.py                        # Boucle de scan des 3 stratégies (mode "main.py")
├── config.py                                 # Configuration centrale (lit le .env)
├── main.py                                     # Point d'entrée du mode simple
├── .env.example
└── requirements.txt
```

## ⚙️ Installation

```bash
cd PocketOptionBot
pip install -r requirements.txt
cp .env.example .env
```

Remplis ton `.env` :
- `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` (tu les as déjà)
- `POCKET_SSID` si tu veux fournir ta session directement, sinon laisse vide
  pour te connecter via la fenêtre de login automatique
- `POCKET_DEMO=true` tant que tu testes (passe à `false` pour le compte réel)
- `POCKET_PAIRS` : liste des paires à surveiller/proposer

## ▶️ Lancer le bot

### Menu interactif (recommandé, comme sur tes captures)
```bash
python telegram_menu_bot.py
```
Envoie `/start` à ton bot Telegram. Tu obtiens :
- Le solde et le mode (Démo/Réel)
- 🚀 Lancer l'auto-trading → choix de la mise ($1/$2/$5/$10) → choix de
  l'actif (auto ou manuel) → choix de la stratégie (5s / 1M / 2M) →
  "Série de 5 deals" (le bouton "Par take-profit" est affiché mais
  verrouillé 🔒 pour l'instant, comme demandé)
- 🎮 Mode manuel → tu reçois uniquement les signaux, à toi de trader

Pendant l'auto-trading : messages édités en direct avec la barre de
progression du trade en cours, le log de chaque étape (deal/étape,
résultat, montant), et le récapitulatif final (deals gagnés/perdus, P&L,
solde avant/après). Un bouton "⏹ Arrêter" permet de couper la série en
cours.

### Mode simple (scan silencieux + notifications)
```bash
python main.py
```

## 🎯 Stratégies

### 5s (avec martingale)
- Bougies normales de 5 secondes, expiration 5s
- RSI 14 : passage sous 30 → signal CALL candidat, passage au-dessus de
  70 → signal PUT candidat
- Confirmation par la mèche de la dernière bougie (`shadow_percentage`,
  30% par défaut, 0 pour désactiver)
- Martingale : x2.2, jusqu'à 5 étapes après une perte

### 1M
- Heikin Ashi 60s, expiration 60s
- Bollinger(20, 2) + SMA(2)/SMA(5) + RSI(8, 30/70), 3 conditions combinées
- Pas de martingale (trade simple)

### 2M
- Heikin Ashi 120s, expiration 120s
- Bollinger(6, 1.3) + MACD(6, 19, 6)
- Pas de martingale (trade simple)

## ⚠️ Avertissements importants

- La martingale amplifie le risque de perte de façon exponentielle. À
  l'étape 5 (x2.2⁴ après la mise de base), la mise vaut environ **51x** la
  mise de base. Fixe toi-même une limite de perte journalière **avant** de
  lancer le mode automatique en réel — le bot ne la devine pas.
- Le trading d'options binaires est à haut risque ; ceci est un outil, pas
  un conseil financier. Teste d'abord longuement en mode démo et en mode
  manuel avant de passer en auto sur un compte réel.
- Je n'ai pas reproduit les éléments publicitaires/de parrainage visibles
  sur les captures d'un autre bot Telegram (pub, lien de parrainage,
  statistiques de gain gonflées) : ce sont des pratiques marketing
  trompeuses, volontairement laissées hors de ce projet.
