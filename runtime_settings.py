"""Réglages que l'administrateur peut changer DEPUIS TELEGRAM (Administration › ⚙️ Réglages), sans éditer `.env`.

Les valeurs sont enregistrées dans <DATA_DIR>/reglages.json et PRIMENT sur `.env`. Avec Docker, ce dossier est le volume
`./data` : les réglages survivent donc aux redémarrages et aux mises à jour du bot hébergé.

Restent dans `.env` (volontairement) : TELEGRAM_TOKEN, ADMIN_IDS, ALLOW_REAL, MAX_REAL_STAKE, les SSID, le navigateur.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("reglages")

# champ de Config -> (libellé, type, secret, aide)
FIELDS: Dict[str, Tuple[str, str, bool, str]] = {
    "bot_name": ("Nom du bot", "str", False, "affiché dans les messages (ex. LEGIT AI)"),
    "image_handle": ("Pseudo sur les images", "str", False, "ex. @LegitTradeAI_bot"),
    "support_url": ("Support", "str", False, "lien https://t.me/… ou @pseudo"),
    "channel_id": ("Canal (vérification)", "str", False, "@nom_du_canal ou -100… (le bot doit en être admin)"),
    "channel_url": ("Canal (lien)", "str", False, "lien d'invitation https://t.me/…"),
    "po_register_url": ("Lien d'inscription Pocket Option", "str", False, "votre lien d'affiliation"),
    "partners_email": ("pocketpartners : e-mail", "str", False, "pour vérifier les ID et les dépôts"),
    "partners_password": ("pocketpartners : mot de passe", "str", True, "votre message est supprimé aussitôt"),
    "partners_login_url": ("pocketpartners : page de connexion", "str", False, "adresse https://…"),
    "partners_stats_url": ("pocketpartners : page des stats", "str", False, "adresse https://…"),
    "po_login_url": ("Pocket Option : page de connexion", "str", False, "adresse https://…"),
    "po_profile_url": ("Pocket Option : page du profil", "str", False, "adresse https://…"),
    "deposit_url": ("Page de dépôt", "str", False, "doit contenir {amount}, ex. https://pocketoption.com/en/cabinet/deposit/?amount={amount}"),
    "verify_mode": ("Vérification des ID", "choice", False, "auto (pocketpartners) · manual (vous validez) · off (aucune)"),
    "require_verified": ("ID vérifié obligatoire", "bool", False, "oui / non"),
    "auto_signup": ("Création de compte automatique", "bool", False, "oui / non"),
    "signup_per_day": ("Créations de compte / jour / personne", "int", False, ""),
    "signup_per_hour_global": ("Créations de compte / heure (total)", "int", False, ""),
    "web_timeout": ("Délai du navigateur (secondes)", "int", False, "10 à 300 ; augmentez si les pages sont lentes"),
    "signal_images": ("Image sous les signaux", "bool", False, "oui / non"),
    "result_cards": ("Carte de résultat en fin de session", "bool", False, "oui / non"),
    "promo_code": ("Code promo", "str", False, "ex. 50START (vide = aucun)"),
    "promo_text": ("Texte du code promo", "str", False, "ex. bonus sur votre dépôt"),
    "deposit_packs": ("Packs de dépôt", "packs", False, "dépôt en $ : jetons, ex. 20:200,50:600,100:1500"),
    "bonus_tokens": ("Bonus : jetons", "int", False, "jetons du bonus quotidien"),
    "bonus_hours": ("Bonus : délai (heures)", "int", False, "0 = bonus unique"),
    "ref_bonus": ("Parrainage : jetons par ami", "int", False, ""),
    "channel_bonus": ("Canal : jetons offerts", "int", False, ""),
    "welcome_tokens": ("Jetons à l'inscription", "int", False, ""),
    "cost_auto_demo": ("Coût session démo", "int", False, "jetons par session d'auto-trading en démo"),
    "cost_auto_real": ("Coût session réelle", "int", False, "jetons par session d'auto-trading en réel"),
}


def parse(key: str, raw: str) -> Any:
    """Valide et convertit la saisie. Lève ValueError avec une explication claire."""
    _, kind, _, _ = FIELDS[key]
    raw = raw.strip()
    if raw in ("-", "aucun", "vide"):
        raw = ""
    if kind == "bool":
        low = raw.lower()
        if low in ("1", "oui", "o", "yes", "y", "on", "true", "✅"):
            return True
        if low in ("0", "non", "n", "no", "off", "false", "❌"):
            return False
        raise ValueError("Répondez « oui » ou « non ».")
    if kind == "choice":
        low = raw.lower()
        if low not in ("auto", "manual", "off"):
            raise ValueError("Répondez auto, manual ou off.")
        return low
    if kind == "int":
        try:
            n = int(raw)
        except ValueError:
            raise ValueError("Envoyez un nombre entier (ex. 10).")
        lo, hi = (10, 300) if key == "web_timeout" else (0, 100000)
        if n < lo or n > hi:
            raise ValueError(f"Envoyez un nombre entre {lo} et {hi}.")
        return n
    if kind == "packs":
        out = []
        for part in raw.replace(" ", "").split(","):
            usd, _, tok = part.partition(":")
            if not (usd.isdigit() and tok.isdigit() and int(usd) > 0):
                raise ValueError("Format : dépôt:jetons séparés par des virgules, ex. 20:200,50:600")
            out.append((int(usd), int(tok)))
        if not out:
            raise ValueError("Format : dépôt:jetons séparés par des virgules, ex. 20:200,50:600")
        return tuple(sorted(out))
    if key == "bot_name" and not raw:
        raise ValueError("Le nom ne peut pas être vide.")
    if key == "deposit_url":
        try:
            ok = raw.startswith("https://") and "{amount}" in raw and raw.format(amount=20)
        except (KeyError, ValueError, IndexError):
            ok = False
        if not ok:
            raise ValueError("Lien https:// qui contient {amount} (et aucune autre accolade).")
    elif key in ("support_url", "channel_url", "po_register_url", "po_login_url", "po_profile_url",
                 "partners_login_url", "partners_stats_url") and (raw or key.startswith(("po_l", "po_p", "partners"))):
        if raw.startswith("@"):
            raw = "https://t.me/" + raw[1:]
        if not raw.startswith(("https://", "http://")):
            raise ValueError("Envoyez un lien complet commençant par https:// (ou @pseudo pour un compte Telegram).")
    if key == "channel_id" and raw and not (raw.startswith("@") or raw.lstrip("-").isdigit()):
        raise ValueError("Envoyez @nom_du_canal ou l'identifiant numérique (-100…).")
    if key == "image_handle" and raw and not raw.startswith("@"):
        raw = "@" + raw
    if len(raw) > 500:
        raise ValueError("Texte trop long (500 caractères maximum).")
    return raw


def display(cfg, key: str, short: bool = False) -> str:
    """Valeur actuelle lisible (les secrets sont masqués)."""
    v = getattr(cfg, key, "")
    _, kind, secret, _ = FIELDS[key]
    if secret:
        return "••••••••" if v else "—"
    if kind == "bool":
        return "✅ oui" if v else "❌ non"
    if kind == "packs":
        v = ",".join(f"{a}:{b}" for a, b in v)
    s = str(v) if v not in ("", None) else "—"
    return (s[:24] + "…") if short and len(s) > 25 else s


def _file(data_dir) -> Path:
    return Path(data_dir) / "reglages.json"


def load(data_dir) -> Dict[str, Any]:
    try:
        raw = json.loads(_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        if k in FIELDS:
            try:
                out[k] = parse(k, ",".join(f"{a}:{b}" for a, b in v) if FIELDS[k][1] == "packs" else str(v))
            except (ValueError, TypeError):
                log.warning("réglage %s ignoré (valeur invalide)", k)
    return out


def apply(cfg, values: Dict[str, Any]) -> None:
    """Applique les valeurs sur l'objet Config EXISTANT (figé : on contourne), pour que tous les modules les voient."""
    for k, v in values.items():
        object.__setattr__(cfg, k, v)


def set_value(cfg, key: str, raw: str) -> Any:
    """Valide, applique tout de suite et enregistre. Retourne la valeur appliquée."""
    value = parse(key, raw)
    apply(cfg, {key: value})
    path = _file(cfg.data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[key] = [list(p) for p in value] if FIELDS[key][1] == "packs" else value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)                                   # peut contenir un mot de passe : lisible par vous seul
    except OSError:
        pass
    os.replace(tmp, path)
    return value
