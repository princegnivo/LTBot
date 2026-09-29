"""
martingale.py
-------------
Gestion de la martingale — UNIQUEMENT pour la stratégie "5s" (comme
demandé). "1M" et "2M" tradent en mise simple, sans aucune relance.

Règle : après une perte sur "5s", la mise suivante = mise précédente x
multiplicateur, MAXIMUM 2 relances après la mise de base. Si on perd
encore après la 2e relance, on arrête la séquence et on attend une
toute nouvelle confirmation (nouveau signal) avant de retrader — on ne
relance jamais une 3e fois sur la même perte. Après une victoire (ou
l'atteinte du max de 2 étapes), on revient à la mise de base.

⚠️ Rappel important : la martingale augmente le risque de perte en
capital de façon exponentielle. Avec x2.2 sur 2 étapes, la mise à
l'étape 2 vaut environ 4.84x la mise de base. Fixe toujours une limite
de perte journalière (stop-loss) en plus de cette logique — le code ne
la devine pas pour toi.
"""

from dataclasses import dataclass


@dataclass
class MartingaleConfig:
    base_amount: float = 1.0
    multiplier: float = 2.2
    max_steps: int = 2           # nombre MAXIMUM de relances APRÈS la mise de base (demandé : 2, pas plus)


class MartingaleManager:
    def __init__(self, config: MartingaleConfig):
        self.cfg = config
        self.current_step = 0
        self.current_amount = config.base_amount

    def reset(self):
        self.current_step = 0
        self.current_amount = self.cfg.base_amount

    def next_amount(self) -> float:
        """Montant à miser pour le prochain trade."""
        return round(self.current_amount, 2)

    def register_result(self, won: bool):
        """
        À appeler après le résultat d'un trade pour préparer la mise
        suivante. Retourne True si la séquence de martingale est terminée
        (victoire, ou nombre d'étapes max atteint sans victoire).
        """
        if won:
            self.reset()
            return True

        if self.current_step >= self.cfg.max_steps:
            # Perte max atteinte : on arrête la séquence et on repart à zéro
            self.reset()
            return True

        self.current_step += 1
        self.current_amount = round(self.current_amount * self.cfg.multiplier, 2)
        return False

    def is_at_max(self) -> bool:
        return self.current_step >= self.cfg.max_steps
