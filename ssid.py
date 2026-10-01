"""Lecture tolérante des SSID Pocket Option (format 42["auth",{...}]).

Cas gérés : guillemets « intelligents » (iPhone/Word), espaces insécables et caractères invisibles, sauts de ligne,
```blocs de code```, texte avant/après, SSID entouré de guillemets, antislashs perdus par une application de
messagerie (réparation automatique), SSID collé avec « 42 » en trop ou sans le préfixe « 42 ».
"""
import json
import re
from typing import Optional, Tuple

_SMART = {"“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"', "″": '"',
          "‘": "'", "’": "'", "\u00a0": " ", "\u2009": " ", "\u202f": " "}
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
MAX_LEN = 4000


def _clean(raw: str) -> str:
    s = (raw or "").translate(_INVISIBLE)
    for bad, good in _SMART.items():
        s = s.replace(bad, good)
    s = s.replace("\r", "").replace("\n", "").replace("\t", "").strip().strip("`").strip()
    if len(s) >= 2 and s[0] == s[-1] == '"' and "auth" in s:        # SSID collé comme une chaîne JSON : "42[\"auth\",…]"
        try:
            inner = json.loads(s)
            if isinstance(inner, str):
                s = inner
        except ValueError:
            s = s[1:-1]
    return s


def _repair(s: str) -> Optional[dict]:
    """Antislashs perdus : "session":"a:4:{s:10:"session_id";…}" n'est plus du JSON ; on relit les champs un à un."""
    m = re.search(r'"session"\s*:\s*"(.*)"\s*,\s*"(?:isDemo|uid|platform|isFastHistory|isOptimized)"', s, re.S)
    if not m:
        return None
    data = {"session": m.group(1).replace('\\"', '"')}
    for key, pat in (("isDemo", r"(\d)"), ("uid", r"(\d+)"), ("platform", r"(\d+)")):
        k = re.search(r'"%s"\s*:\s*%s' % (key, pat), s)
        if k:
            data[key] = int(k.group(1))
    for key in ("isFastHistory", "isOptimized"):
        k = re.search(r'"%s"\s*:\s*(true|false)' % key, s)
        if k:
            data[key] = k.group(1) == "true"
    return data


def parse_ssid(raw: str) -> Tuple[str, Optional[bool], str]:
    """Retourne (ssid_normalisé, is_demo | None si absent, erreur). erreur == "" si tout va bien."""
    if len(raw or "") > MAX_LEN:
        return "", None, "texte beaucoup trop long : envoyez uniquement le SSID"
    s = _clean(raw)
    i = s.find("42[")
    if i == -1:
        j = s.find('["auth"')
        if j == -1:
            return "", None, 'il doit ressembler à 42["auth",{…}]'
        s = "42" + s[j:]
    else:
        s = s[i:]
    j = s.rfind("]")
    if j == -1:
        return "", None, "incomplet (il doit finir par ]) — le message a peut-être été coupé"
    s = s[: j + 1]
    data, repaired = None, False
    try:
        arr = json.loads(s[2:])
        if arr[0] == "auth" and isinstance(arr[1], dict):
            data = arr[1]
    except (ValueError, IndexError, TypeError):
        pass
    if data is None:
        data, repaired = _repair(s), True
    if not data or not isinstance(data.get("session"), str) or len(data["session"]) < 8:
        return "", None, "contenu illisible ou incomplet (guillemets modifiés ? texte coupé ?)"
    if repaired:
        s = "42" + json.dumps(["auth", data], separators=(",", ":"), ensure_ascii=False)
    is_demo = None if "isDemo" not in data else bool(data["isDemo"])
    return s, is_demo, ""


def po_uid(ssid: str) -> Optional[int]:
    """Identifiant du compte Pocket Option contenu dans le SSID (pour l'afficher, jamais le SSID lui-même)."""
    m = re.search(r'"uid"\s*:\s*(\d+)', ssid or "")
    return int(m.group(1)) if m else None
