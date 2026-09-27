# PO Bot (Python)

Utilise la lib [Mastaaa1987/PocketOptionAPI-v2](https://github.com/Mastaaa1987/PocketOptionAPI-v2).

## Installation
```
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sous Windows
pip install -r requirements.txt
cp .env.example .env
# remplir TELEGRAM_BOT_TOKEN et, si tu veux éviter le login interactif, PO_SSID
python telegram_bot.py
```

## Fonctionnel
- Bot Telegram avec les boutons de tes captures : mode, mise ($1/$2/$5/$10), sélection actif, série (5 deals) vs take-profit.
- RSI + mèche (`indicators.py`), paramètre "Pourcentage d'ombre" 30% par défaut.
- Martingale x2, 4 paliers max (`martingale.py`).
- `trading_engine.py` branche directement sur `pocketoptionapi.stable_api.PocketOption`.

## À vérifier avant de lancer en vrai (je n'ai pas le code source de la lib, juste son README)
1. **Format de `GetHistory()`** : je suppose des clés `open/high/low/close/time`. Fais un `print(api.GetHistory("EURUSD_otc"))` isolé pour confirmer, et ajuste `_normalize_candles()` dans `trading_engine.py` si besoin.
2. **Valeurs acceptées par `Buy()` pour `action`** : le README montre `'put'`, donc `'call'`/`'put'` — à confirmer aussi.
3. **Connexion** : la lib récente ouvre une fenêtre `pywebview` pour te connecter (pas besoin de SSID manuel). Si tu tournes sur un serveur sans interface graphique, ça ne marchera pas tel quel — dans ce cas utilise `PO_SSID` dans `.env` (capturé comme avant via DevTools), le code tente de le forcer via `global_value.SSID`, mais ce n'est pas garanti selon la version exacte de la lib.
4. **`take-profit`** : le mode "Par take-profit" n'est pour l'instant pas branché dans `run_trading_loop` — seul "Série (5 deals)" tourne. Dis-moi si tu veux que je l'ajoute.

## Sécurité
- Ne mets jamais ton SSID/token Telegram ailleurs que dans `.env`.
- Le repo est un projet tiers non officiel (aucun lien avec Pocket Option) — vérifie son code avant de lui donner tes identifiants si tu passes en compte réel un jour.
