"""
martingale.py
-------------
Gestion de la martingale pour la stratégie "5s".

Règle : après une perte, la mise suivante = mise précédente x multiplicateur,
jusqu'à `max_steps` étapes. Après une victoire (ou l'atteinte du max
d'étapes), on revient à la mise de base.

⚠️ Rappel important : la martingale augmente exponentiellement le risque de
perte en capital. Avec x2.2 sur 5 étapes, la mise à l'étape 5 vaut environ
51x la mise de base (2.2^4). Fixe toujours une limite de perte journalière
(stop-loss) en plus de cette logique — le code ne la devine pas pour toi.
"""

from dataclasses import dataclass


@dataclass
class MartingaleConfig:
    base_amount: float = 1.0
    multiplier: float = 2.2
    max_steps: int = 5           # nombre d'étapes APRÈS la mise de base


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
