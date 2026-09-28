"""
pairs.py
--------
Sélection des paires de devises à proposer au bot.

Demandé explicitement : ne proposer QUE les paires dont le payout est de
92%, en mode manuel comme en mode automatique. En automatique, la paire
est choisie AU HASARD parmi ces paires à 92% (pas "la meilleure
confiance" — un choix aléatoire, comme demandé).
"""

import random
from typing import List, Optional

import config

TARGET_PAYOUT = 92  # % — seul payout accepté, comme demandé


def get_eligible_pairs(api) -> List[str]:
    """
    Interroge l'API PocketOption pour la liste réelle des paires
    actuellement ouvertes au trading, et ne garde que celles dont le
    payout est EXACTEMENT 92%.

    Retombe sur la liste statique de .env (config.PAIRS) filtrée par
    payout connu si l'appel API échoue (ex: pas encore connecté),
    pour ne jamais planter le bot.
    """
    try:
        data = api.GetPairs()  # {nom_paire: {"id":.., "payout":.., "type":.., "active": bool}}
    except Exception:
        data = None

    if data:
        eligible = [
            name
            for name, info in data.items()
            if info.get("active", True) and int(info.get("payout", 0)) == TARGET_PAYOUT
        ]
        if eligible:
            return sorted(eligible)

    # Repli : liste configurée manuellement, on garde tout (impossible de
    # vérifier le payout sans l'API), à utiliser seulement si l'API est
    # indisponible.
    return [p.strip() for p in config.PAIRS if p.strip()]


def pick_random_pair(api) -> Optional[str]:
    """Choix ALÉATOIRE d'une paire parmi celles à 92% de payout."""
    eligible = get_eligible_pairs(api)
    if not eligible:
        return None
    return random.choice(eligible)
