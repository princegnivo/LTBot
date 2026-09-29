"""
candles.py
----------
Correctif d'un bug de fond du projet : aucune partie du code n'appelait
jamais `change_symbol` / `get_candles` sur l'API PocketOption. Résultat :
`api.get_dataframe(pair, period)` ne recevait JAMAIS de bougies (la
structure interne `global_value.pairs[pair]` restait vide), donc AUCUNE
stratégie ne pouvait jamais recevoir de données à analyser — d'où
"aucun signal, aucun trade", même quand les conditions de marché
auraient dû déclencher une confirmation.

`api.get_candles(pair, period)` fait deux choses nécessaires :
    1. Il s'abonne au flux de prix de la paire (nécessaire pour que les
       ticks en direct continuent d'arriver ensuite).
    2. Il précharge un historique de bougies, pour ne pas avoir à
       attendre 30+ minutes de flux en direct avant d'avoir assez de
       bougies pour une stratégie (30 bougies mini pour 1M ≈ 30 min).

Ce module garde un cache pour ne PAS rappeler `get_candles` (bloquant,
plusieurs secondes) à chaque scan — seulement au premier scan d'une
paire, puis à un intervalle de rafraîchissement raisonnable.
"""

import time
from typing import Dict, Tuple

_last_primed: Dict[Tuple[str, int], float] = {}
REFRESH_SECONDS = 600  # on réamorce l'historique toutes les 10 minutes


def ensure_ready(api, pair: str, period: int) -> None:
    """S'assure qu'on est abonné et qu'un historique existe pour pair/period."""
    key = (pair, period)
    now = time.time()
    last = _last_primed.get(key, 0)
    if now - last < REFRESH_SECONDS:
        return
    try:
        api.get_candles(pair, period)
        _last_primed[key] = now
    except Exception:
        # On ne bloque jamais le bot pour un échec de préchargement ;
        # le prochain scan retentera.
        pass
