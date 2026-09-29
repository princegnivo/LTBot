# Bot Telegram de trading — Pocket Option

Détecte vos stratégies **1M / 2M / 5s**, envoie les signaux sur Telegram et peut passer les ordres
automatiquement (plusieurs positions en parallèle), avec seuils de gain et de risque.

## Installation
```bash
pip install -r requirements.txt
cp .env.example .env      # renseignez TELEGRAM_TOKEN et ALLOWED_USER_IDS
python main.py
```
1. Créez le bot avec @BotFather, mettez le token dans `.env`.
2. Envoyez `/id` à votre bot, mettez le nombre obtenu dans `ALLOWED_USER_IDS` (le bot refuse tous les autres).
3. Récupérez votre SSID démo (`python get_ssid.py demo`) puis envoyez `/ssid demo <SSID>` au bot
   (le message est supprimé). `BROKER=paper` permet de tout tester sans compte.

## Menus
🚀 Auto-trading (mise → actif auto/manuel → Série de N deals ou Take-profit) · 📡 Signaux seuls ·
🎮 Manuel (marché → catégorie → top 6 paiements → analyse → HAUSSE/BAISSE, expiration et mise) ·
📊 Compte · 📈 Stats · ⚙️ Paramètres (stratégies, actifs, mise/martingale, risque, notifications) · ❓ Aide.
Commandes : `/stop` coupe la session, `/ssid`, `/id`.

## Fonctionnement
- **Entrée ultra-rapide** : chaque flux de bougies est évalué à chaque mise à jour (≈ 8 fois/s max) et l'ordre part
  immédiatement (latence signal→ordre affichée). Option « à la clôture » si vous préférez éviter les signaux qui
  disparaissent en cours de bougie.
- **Positions multiples** : `max_open` deals en parallèle, `max_open_per_asset` par actif, délai mini entre entrées.
- **Martingale** (1M et 5s ; jamais sur 2M) : méthode « récupération » (perte cumulée + gain visé) ou multiplicateur.
- **Seuils** : take-profit / stop-loss de session, deals perdus d'affilée, exposition max, taille max d'une étape.
  ⚠️ Une option binaire ne se clôture pas avant l'expiration : au seuil, le bot n'ouvre plus rien et laisse les
  positions en cours aller à terme.
- **Mode RÉEL** verrouillé par défaut (`ALLOW_REAL=1`), confirmation par bouton, plafond `MAX_REAL_STAKE`.

## Structure
`strategies/` (vos 3 fichiers, inchangés + registre) · `indicators.py` · `engine.py` (scanner, positions, seuils) ·
`broker.py` (BinaryOptionsToolsV2 + simulateur) · `bot/app.py` (Telegram) · `views.py` · `store.py` ·
`get_ssid.py` (seule pièce reprise de PocketOptionAPI-v2) · `tests/`.

Tests : `PYTHONPATH=. python tests/test_engine.py` et `PYTHONPATH=. python tests/test_bot_flow.py`.

## Avertissement
Bibliothèques non officielles, sans garantie. Avec un paiement de 92 %, il faut gagner plus de ~52 % des positions
pour ne pas perdre d'argent ; la martingale ne change pas cette espérance et amplifie les pertes. Ceci n'est pas un
conseil financier : validez vos stratégies en démo sur un grand nombre de trades avant tout argent réel.
