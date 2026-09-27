"""
signal.py
---------
Structure de données commune renvoyée par toutes les stratégies.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Signal:
    strategy: str            # "5s" ou "2M"
    pair: str                 # ex: "EURUSD_otc"
    direction: str             # "call" ou "put"
    confidence: int             # 0 à 100
    expiration: int              # en secondes
    reason: str                   # explication courte (lisible humain)
    price: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def emoji(self) -> str:
        return "🟢" if self.direction == "call" else "🔴"

    def to_telegram_text(self) -> str:
        return (
            f"{self.emoji()} <b>{self.direction.upper()}</b> — {self.pair}\n"
            f"Stratégie : {self.strategy} | Expiration : {self.expiration}s\n"
            f"Confiance : {self.confidence}/100\n"
            f"Raison : {self.reason}\n"
            f"Heure (UTC) : {self.created_at.strftime('%H:%M:%S')}"
        )
