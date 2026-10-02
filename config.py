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


def _packs(name: str, default: str) -> tuple:
    """« 20:200,50:600 » -> ((20, 200), (50, 600)) : dépôt en $ -> jetons."""
    out = []
    for part in os.getenv(name, default).replace(" ", "").split(","):
        usd, _, tok = part.partition(":")
        if usd.isdigit() and tok.isdigit():
            out.append((int(usd), int(tok)))
    return tuple(sorted(out))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_ids: frozenset     # hérité : accès ouvert à tous ; sert seulement d'admins si ADMIN_IDS est vide
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
    # ---- marque / textes ------------------------------------------------------------
    bot_name: str = "LEGIT AI"
    welcome_text: str = ""          # remplace la 1re ligne d'accueil si renseigné
    faq_file: str = ""              # fichier texte de la FAQ (défaut : faq.txt à la racine)
    # ---- inscription / vérification d'affiliation -------------------------------------------
    require_verified: bool = True   # 1 = il faut un ID Pocket Option vérifié (via votre lien) pour utiliser le bot
    verify_mode: str = "manual"     # auto (pocketpartners) | manual (validation admin) | off
    po_register_url: str = ""       # votre lien d'affiliation (bouton S'INSCRIRE + création automatique)
    po_login_url: str = "https://pocketoption.com/en/login/"
    po_profile_url: str = "https://pocketoption.com/en/cabinet/profile/"
    po_session_cookie: str = "ci_session"
    partners_login_url: str = "https://pocketpartners.com/en/login"
    partners_stats_url: str = "https://pocketpartners.com/en/statistics?#traders"
    partners_email: str = ""
    partners_password: str = ""
    auto_signup: bool = True        # création automatique de compte (Playwright)
    signup_per_day: int = 3         # essais de création par utilisateur et par jour
    signup_per_hour_global: int = 30
    web_headless: bool = True
    web_timeout: int = 45           # secondes par étape de navigation
    web_concurrency: int = 2        # navigateurs simultanés
    web_proxy: str = ""
    selectors_file: str = ""        # JSON qui remplace les sélecteurs par défaut (voir README)
    # ---- dépôts, bonus -----------------------------------------------------------------
    deposit_url: str = "https://pocketoption.com/en/cabinet/deposit/?amount={amount}"
    deposit_packs: tuple = ((20, 200), (50, 600), (100, 1500), (250, 4000), (500, 10000))
    promo_code: str = ""
    promo_text: str = "bonus sur votre dépôt"
    bonus_tokens: int = 5
    bonus_hours: int = 24           # 0 = bonus unique


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
    p_email, p_pass = os.getenv("PARTNERS_EMAIL", "").strip(), os.getenv("PARTNERS_PASSWORD", "")
    mode = os.getenv("VERIFY_MODE", "").strip().lower()
    if mode not in ("auto", "manual", "off"):
        mode = "auto" if (p_email and p_pass) else "manual"
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
        bot_name=os.getenv("BOT_NAME", "LEGIT AI").strip() or "LEGIT AI",
        welcome_text=os.getenv("WELCOME_TEXT", "").strip(),
        faq_file=os.getenv("FAQ_FILE", "").strip(),
        require_verified=_bool("REQUIRE_VERIFIED", True),
        verify_mode=mode,
        po_register_url=os.getenv("PO_REGISTER_URL", "").strip(),
        po_login_url=os.getenv("PO_LOGIN_URL", "https://pocketoption.com/en/login/").strip(),
        po_profile_url=os.getenv("PO_PROFILE_URL", "https://pocketoption.com/en/cabinet/profile/").strip(),
        po_session_cookie=os.getenv("PO_SESSION_COOKIE", "ci_session").strip(),
        partners_login_url=os.getenv("PARTNERS_LOGIN_URL", "https://pocketpartners.com/en/login").strip(),
        partners_stats_url=os.getenv("PARTNERS_STATS_URL", "https://pocketpartners.com/en/statistics?#traders").strip(),
        partners_email=p_email,
        partners_password=p_pass,
        auto_signup=_bool("AUTO_SIGNUP", True),
        signup_per_day=_int("SIGNUP_PER_DAY", 3),
        signup_per_hour_global=_int("SIGNUP_PER_HOUR_GLOBAL", 30),
        web_headless=_bool("WEB_HEADLESS", True),
        web_timeout=_int("WEB_TIMEOUT", 45),
        web_concurrency=max(1, _int("WEB_CONCURRENCY", 2)),
        web_proxy=os.getenv("WEB_PROXY", "").strip(),
        selectors_file=os.getenv("PO_SELECTORS_FILE", "").strip(),
        deposit_url=os.getenv("DEPOSIT_URL", "https://pocketoption.com/en/cabinet/deposit/?amount={amount}").strip(),
        deposit_packs=_packs("DEPOSIT_PACKS", "20:200,50:600,100:1500,250:4000,500:10000") or ((20, 200),),
        promo_code=os.getenv("PROMO_CODE", "").strip(),
        promo_text=os.getenv("PROMO_TEXT", "bonus sur votre dépôt").strip(),
        bonus_tokens=_int("BONUS_TOKENS", 5),
        bonus_hours=_int("BONUS_HOURS", 24),
    )
