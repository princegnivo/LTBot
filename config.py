"""Configuration : variables d'environnement (+ fichier .env optionnel, sans dépendance)."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "oui", "on")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_ids: frozenset
    data_dir: Path
    broker: str              # "pocketoption" | "paper" (simulation locale sans compte)
    allow_real: bool         # verrou : le mode RÉEL est refusé tant que ALLOW_REAL != 1
    max_real_stake: float    # plafond de mise en RÉEL
    ssid_demo: str
    ssid_real: str


def load_config() -> Config:
    ids = frozenset(int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x)
    data = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    data.mkdir(parents=True, exist_ok=True)
    return Config(
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        allowed_user_ids=ids,
        data_dir=data,
        broker=os.getenv("BROKER", "pocketoption").strip().lower(),
        allow_real=_bool("ALLOW_REAL", False),
        max_real_stake=float(os.getenv("MAX_REAL_STAKE", "20")),
        ssid_demo=os.getenv("PO_SSID_DEMO", ""),
        ssid_real=os.getenv("PO_SSID_REAL", ""),
    )
