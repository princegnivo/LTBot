import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    strategy: str        # "1M" | "2M" | "5s"
    pair: str            # symbole Pocket Option (ex. EURUSD_otc)
    direction: str       # "call" | "put"
    confidence: int      # 0-100
    expiration: int      # secondes
    reason: str
    price: float
    created_at: float = field(default_factory=time.time)
