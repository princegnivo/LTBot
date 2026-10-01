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
        os.environ.setdefault(k.strip(), v.strip().split("  #")[0].strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "oui", "on")


def _ids(name: str) -> frozenset:
    return frozenset(int(x) for x in os.getenv(name, "").replace(" ", "").split(",") if x.lstrip("-").isdigit())


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_ids: frozenset     # comptes autorisés (non admins, sauf si ADMIN_IDS est vide : voir load_config)
    data_dir: Path
    broker: str                     # "pocketoption" | "paper" (simulation locale sans compte)
    allow_real: bool                # verrou : le mode RÉEL est refusé tant que ALLOW_REAL != 1
    max_real_stake: float           # plafond de mise en RÉEL
    ssid_demo: str
    ssid_real: str
    admin_ids: frozenset = frozenset()
    channel_id: str = ""            # canal principal (@nom ou -100…) : vérification d'abonnement
    channel_url: str = ""           # lien d'invitation du canal
    support_url: str = ""           # lien du support (https://t.me/…)
    bot_username: str = ""          # facultatif : sinon détecté automatiquement
    channel_bonus: int = 10         # jetons offerts à la vérification du canal (une seule fois)
    ref_bonus: int = 10             # jetons offerts pour chaque ami invité
    welcome_tokens: int = 0         # jetons offerts à la création d'un compte
    cost_auto_demo: int = 1         # jetons consommés par session d'auto-trading (démo)
    cost_auto_real: int = 1         # jetons consommés par session d'auto-trading (réel)
    referral_auth: bool = True      # un ami qui arrive par lien de parrainage est autorisé automatiquement


def load_config() -> Config:
    ids = _ids("ALLOWED_USER_IDS")
    admins = _ids("ADMIN_IDS") or ids       # compatibilité : sans ADMIN_IDS, les IDs déjà autorisés restent admins
    data = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    data.mkdir(parents=True, exist_ok=True)
    chan = os.getenv("CHANNEL_ID", "").strip()
    chan_url = os.getenv("CHANNEL_URL", "").strip() or (f"https://t.me/{chan.lstrip('@')}" if chan.startswith("@") else "")
    sup = os.getenv("SUPPORT_URL", "").strip() or os.getenv("SUPPORT_USERNAME", "").strip()
    if sup and not sup.startswith("http"):
        sup = f"https://t.me/{sup.lstrip('@')}"
    return Config(
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        allowed_user_ids=ids,
        data_dir=data,
        broker=os.getenv("BROKER", "pocketoption").strip().lower(),
        allow_real=_bool("ALLOW_REAL", False),
        max_real_stake=float(os.getenv("MAX_REAL_STAKE", "20")),
        ssid_demo=os.getenv("PO_SSID_DEMO", ""),
        ssid_real=os.getenv("PO_SSID_REAL", ""),
        admin_ids=admins,
        channel_id=chan,
        channel_url=chan_url,
        support_url=sup,
        bot_username=os.getenv("BOT_USERNAME", "").strip().lstrip("@"),
        channel_bonus=_int("CHANNEL_BONUS", 10),
        ref_bonus=_int("REF_BONUS", 10),
        welcome_tokens=_int("WELCOME_TOKENS", 0),
        cost_auto_demo=_int("COST_AUTO_DEMO", 1),
        cost_auto_real=_int("COST_AUTO_REAL", 1),
        referral_auth=_bool("REFERRAL_AUTH", True),
    )
