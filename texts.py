"""Textes du bot modifiables sans toucher au code.

Les textes par défaut sont définis ici ; ceux que vous modifiez sont enregistrés dans  <DATA_DIR>/textes.json
(créé automatiquement). Deux façons de les changer :
  • depuis Telegram : Administration › ✏️ Textes (c'est ce qui marche aussi une fois le bot hébergé) ;
  • à la main : ouvrez data/textes.json (modèle : textes.exemple.json à la racine du projet).
Le fichier est relu automatiquement : pas besoin de redémarrer le bot.

Dans un texte, vous pouvez utiliser du HTML Telegram (<b>gras</b>, <i>italique</i>, <code>code</code>, <u>, <s>)
et les {variables} indiquées pour chaque texte. Un texte invalide (variable inconnue, balise mal fermée) est ignoré :
le bot utilise alors le texte par défaut, il ne plante jamais.
"""
import json
import logging
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("texts")

# clé -> (libellé, variables disponibles, texte par défaut)
REGISTRY: Dict[str, tuple] = {
    "welcome": ("👋 Accueil (1re ligne de /start)", ["bot_name"],
                "👋 Salut ! Je suis {bot_name}. Bienvenue dans le monde des millionnaires 🤑💰."),
    "menu_user": ("🏠 Menu principal (utilisateur)", ["bot", "mode_icon", "mode", "tokens"],
                  "🤖 <b>{bot}</b>\n\n{mode_icon} Mode : <b>{mode}</b>\n💎 Jetons : <b>{tokens}</b>"),
    "menu_admin": ("🏠 Menu principal (admin)", ["mode_label", "stake", "strategies"],
                   "🏠 <b>Menu</b>\n\n{mode_label} · mise ${stake} · stratégies {strategies}"),
    "tokens": ("💎 Écran Jetons", ["cost_real", "cost_demo", "tokens"],
               "💎 <b>Jetons</b>\n\n"
               "Un jeton est le carburant de l'auto-trading (le bot trade tout le cycle pour vous).\n"
               "• Session réelle complète : <b>{cost_real}</b> · démo : <b>{cost_demo}</b>\n"
               "• Trading manuel — <b>gratuit</b>\n\n"
               "Vous avez : <b>{tokens}</b> jetons.\n\n"
               "🎁 <b>Jetons pour dépôts :</b>"),
    "deposit": ("💰 Écran Dépôt", [],
                "💰 <b>Dépôt</b>\n\nDéposez sur votre compte Pocket Option et recevez des jetons :"),
    "deposit_after": ("💰 Dépôt — phrase du bas", [],
                      "Après votre dépôt, touchez « J'ai déposé » : je vérifie et je crédite vos jetons."),
    "friends": ("👥 Écran Amis", ["ref_bonus", "link_block", "invited", "earned"],
                "👥 <b>Invitez vos amis</b>\n\nPour chaque ami : <b>+{ref_bonus} jetons</b> pour vous.\n\n"
                "{link_block}Invités : <b>{invited}</b> · gagné : <b>{earned}</b> jetons"),
    "no_tokens": ("💎 Plus de jetons", [],
                  "💎 <b>Plus de jetons</b>\n\nL'auto-trading consomme 1 jeton par session. "
                  "Les signaux et le trading manuel restent gratuits.\n\nGagnez-en ou demandez-en au support :"),
    "settings": ("⚙️ Écran Paramètres", [], "⚙️ <b>Paramètres</b>"),
    "share": ("📊 Partage du total (image admin)", ["period", "amount", "users", "trades"],
              "📊 {period}, les utilisateurs ont déjà tradé <b>{amount}</b> !\n\n"
              "Teste en démo — appuie sur le bouton 🚀\n\n"
              "<i>Résultat net réel de {users} utilisateurs ({trades} trades, démo et réel). "
              "Les résultats passés ne garantissent pas les résultats futurs ; risque de perte en capital.</i>"),
    "faq": ("❓ FAQ", [], ""),                     # vide = contenu du fichier faq.txt
}

_path: Optional[Path] = None
_cache: Dict[str, str] = {}
_mtime: float = -1.0
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "tg-spoiler"}


def configure(data_dir) -> None:
    global _path, _mtime
    _path = Path(data_dir) / "textes.json"
    _mtime = -1.0


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.ok = [], True

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            self.ok = False
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.ok = False


def html_valid(text: str) -> bool:
    """Le HTML est-il accepté par Telegram (balises autorisées, bien fermées) ?"""
    c = _Checker()
    try:
        c.feed(text)
        c.close()
    except Exception:
        return False
    return c.ok and not c.stack


class _Safe(dict):
    def __missing__(self, key):
        raise KeyError(key)


def check(key: str, value: str) -> Optional[str]:
    """None si le texte est utilisable, sinon une explication."""
    if key not in REGISTRY:
        return "texte inconnu"
    if key == "faq":
        return None                                  # FAQ : texte simple (affiché tel quel, sans HTML)
    allowed = set(REGISTRY[key][1])
    for name in re.findall(r"\{(\w*)\}", value):
        if name not in allowed:
            return f"variable {{{name}}} inconnue" + (f" (disponibles : {', '.join('{' + v + '}' for v in sorted(allowed))})" if allowed else " (aucune variable pour ce texte)")
    try:
        value.format_map(_Safe({k: "x" for k in allowed}))
    except (KeyError, ValueError, IndexError) as e:
        return f"accolades invalides ({e})"
    if not html_valid(value.format_map(_Safe({k: "x" for k in allowed}))):
        return "balises HTML invalides (ou mal fermées)"
    return None


def _load() -> Dict[str, str]:
    global _cache, _mtime
    if _path is None:
        return {}
    try:
        m = _path.stat().st_mtime
    except OSError:
        _cache, _mtime = {}, -1.0
        return _cache
    if m != _mtime:
        try:
            raw = json.loads(_path.read_text(encoding="utf-8"))
            _cache = {k: v for k, v in raw.items() if isinstance(v, str) and k in REGISTRY} if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            log.warning("textes.json illisible : textes par défaut utilisés")
            _cache = {}
        _mtime = m
    return _cache


def override(key: str) -> Optional[str]:
    return _load().get(key) or None


def get(key: str, default: Optional[str] = None, **vars) -> str:
    """Texte final : version modifiée si elle est valide, sinon `default`, sinon le texte par défaut du registre."""
    base = default if default is not None else REGISTRY[key][2]
    custom = override(key)
    for text in ([custom] if custom else []) + [base]:
        try:
            if text is custom and check(key, text):
                log.warning("texte %r invalide, texte par défaut utilisé", key)
                continue
            return text.format_map(_Safe(vars)) if key != "faq" else text
        except (KeyError, ValueError, IndexError):
            continue
    return base


def save(key: str, value: str) -> None:
    """Enregistre (ou, si `value` est vide, supprime) la version modifiée d'un texte."""
    if _path is None:
        raise RuntimeError("textes non configurés")
    data = dict(_load())
    if value:
        data[key] = value
    else:
        data.pop(key, None)
    _path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _path)
    global _mtime
    _mtime = -1.0


def example() -> str:
    """Contenu du modèle `textes.exemple.json` (tous les textes avec leurs variables)."""
    out = {}
    for k, (label, vars_, default) in REGISTRY.items():
        out[k] = default                    # « faq » reste vide : le contenu de faq.txt est utilisé
    return json.dumps({"_aide": "Copiez ce fichier en data/textes.json et gardez seulement les textes à changer. "
                                "HTML : <b> <i> <u> <s> <code>. Variables entre accolades, ex. {tokens}.",
                       **out}, ensure_ascii=False, indent=2)
