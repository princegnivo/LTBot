"""Persistance JSON : réglages par utilisateur, SSID (chmod 600) et journal des trades."""
import json
import shutil
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class UserSettings:
    # compte / mise
    mode: str = "demo"                 # "demo" | "real"
    stake: float = 1.0
    # stratégies
    strategies: List[str] = field(default_factory=lambda: ["1M", "2M", "5s", "5M"])
    speed: str = "ultra"               # "ultra" | "rapide" | "normal" (bougie en formation) | "cloture" (à la clôture)
    # actifs
    market: str = "otc"                # "otc" | "real"
    asset_mode: str = "auto"           # "auto" (meilleurs paiements) | "manual"
    asset: str = ""                    # symbole si manual
    scan_assets: int = 2               # nb d'actifs surveillés en auto
    min_payout: int = 92               # paiement minimum (92 = seulement les actifs à 92 %)
    currencies_only: bool = True       # auto : devises uniquement (pas d'actions ni de crypto)
    # session
    session_mode: str = "series"       # "series" | "tp"
    series_deals: int = 5
    take_profit: float = 20.0          # $ (0 = désactivé)
    stop_loss: float = 30.0            # $ (0 = désactivé)
    # martingale : après une perte, on attend la confirmation suivante de la même stratégie, puis mise x2,1 ; x2,2 ; x2,3 ; x2,4 ; x2,5
    mg_enabled: bool = True
    mg_other_pair: bool = True         # après une perte, la martingale attend une confirmation sur une AUTRE paire
    # positions multiples (une seule position à la fois par stratégie)
    max_open: int = 4
    max_open_per_asset: int = 1
    cooldown_s: int = 3                # délai mini entre deux entrées sur un même actif
    # filtres internes (non exposés)
    min_confidence: int = 60
    trend_filter: bool = False


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._users_path = self.dir / "users.json"
        self._secrets_path = self.dir / "secrets.json"
        self._trades_path = self.dir / "trades.jsonl"
        self._acc_path = self.dir / "accounts.json"
        self._acc: Dict[str, dict] = self._read(self._acc_path)
        self._users: Dict[str, dict] = self._read(self._users_path)
        self._secrets: Dict[str, dict] = self._read(self._secrets_path)
        self._meta_path = self.dir / "ssid_meta.json"          # état de chaque SSID (jamais le SSID lui-même)
        self._meta: Dict[str, dict] = self._read(self._meta_path)

    # ---- io atomique --------------------------------------------------------
    @staticmethod
    def _read(path: Path) -> dict:
        """Lit un JSON ; s'il est corrompu on tente la copie .bak puis on met le fichier de côté (jamais écrasé)."""
        for cand in (path, path.with_name(path.name + ".bak")):
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except FileNotFoundError:
                continue
            except (ValueError, OSError):
                if cand == path:
                    try:
                        path.replace(path.with_name(f"{path.name}.corrompu-{int(time.time())}"))
                    except OSError:
                        pass
        return {}

    @staticmethod
    def _write(path: Path, data: dict, private: bool = False) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        if private:
            os.chmod(tmp, 0o600)
        if path.exists():                                   # copie de sauvegarde avant chaque remplacement
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass
        os.replace(tmp, path)

    # ---- réglages -----------------------------------------------------------
    def settings(self, uid: int) -> UserSettings:
        raw = dict(self._users.get(str(uid), {}))
        if raw and raw.get("_v", 1) < 2:                      # migration : nouveaux défauts (92 % / devises)
            raw["min_payout"] = 92
            raw["scan_assets"] = min(int(raw.get("scan_assets", 2)), 2)
        if raw and raw.get("_v", 1) < 3:                      # migration : martingale de la vidéo (×2,2, 6 étapes)
            pass
        if raw and raw.get("_v", 1) < 4:                      # migration : stratégie 5M ajoutée, martingale par confirmation
            if "5M" not in raw.get("strategies", []):
                raw["strategies"] = list(raw.get("strategies", [])) + ["5M"]
            raw["mg_enabled"] = True
        known = {f.name for f in fields(UserSettings)}
        return UserSettings(**{k: v for k, v in raw.items() if k in known})

    def save_settings(self, uid: int, s: UserSettings) -> None:
        self._users[str(uid)] = {**asdict(s), "_v": 4}
        self._write(self._users_path, self._users)

    # ---- SSID ---------------------------------------------------------------
    def set_ssid(self, uid: int, mode: str, ssid: str) -> None:
        self._secrets.setdefault(str(uid), {})[mode] = ssid
        self._write(self._secrets_path, self._secrets, private=True)

    def del_ssid(self, uid: int, mode: str) -> None:
        if self._secrets.get(str(uid), {}).pop(mode, None) is not None:
            self._write(self._secrets_path, self._secrets, private=True)
        if self._meta.get(str(uid), {}).pop(mode, None) is not None:
            self._write(self._meta_path, self._meta, private=True)

    def ssid_meta(self, uid: int, mode: str) -> dict:
        """{"saved": ts, "verified": ts (0 = jamais), "error": str, "po_uid": int|None}"""
        return dict(self._meta.get(str(uid), {}).get(mode, {}))

    def set_ssid_meta(self, uid: int, mode: str, **kw) -> None:
        rec = self._meta.setdefault(str(uid), {}).setdefault(mode, {})
        rec.update(kw)
        self._write(self._meta_path, self._meta, private=True)

    def get_ssid(self, uid: int, mode: str) -> str:
        return self._secrets.get(str(uid), {}).get(mode, "")

    # ---- journal des trades ---------------------------------------------------
    def log_trade(self, uid: int, rec: Dict[str, Any]) -> None:
        rec = {"ts": time.time(), "uid": uid, **rec}
        with open(self._trades_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def trades(self, uid: int, since: float = 0.0) -> List[dict]:
        out = []
        try:
            with open(self._trades_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("uid") == uid and r.get("ts", 0) >= since:
                        out.append(r)
        except FileNotFoundError:
            pass
        return out

    def all_trades(self, since: float = 0.0) -> List[dict]:
        out = []
        try:
            with open(self._trades_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("ts", 0) >= since:
                        out.append(r)
        except FileNotFoundError:
            pass
        return out

    # ---- comptes, jetons, parrainage -------------------------------------------------
    def _save_acc(self) -> None:
        self._write(self._acc_path, self._acc)

    def account(self, uid: int) -> dict:
        """Copie de l'enregistrement du compte (valeurs par défaut incluses, sans rien créer)."""
        base = dict(auth=False, blocked=False, tokens=0, name="", username="", joined=0.0, ref_by=None,
                    invited=[], ref_earned=0, channel_ok=False,
                    po_id="", verified=False, verify_status="none", verify_at=0.0, verify_source="",
                    consent=0.0, bonus_at=0.0, dep_credited=0.0)
        base.update(self._acc.get(str(uid), {}))
        return base

    def has_account(self, uid: int) -> bool:
        return str(uid) in self._acc

    def ensure_account(self, uid: int, name: str = "", username: str = "", welcome: int = 0) -> bool:
        """Crée le compte si besoin (jetons de bienvenue) et met à jour le profil. True si créé."""
        key = str(uid)
        rec = self._acc.get(key)
        created = rec is None
        if created:
            rec = self._acc[key] = {"joined": time.time(), "tokens": max(0, int(welcome))}
        changed = created
        if name and rec.get("name") != name:
            rec["name"], changed = name, True
        if rec.get("username", "") != (username or ""):
            rec["username"], changed = username or "", True
        if changed:
            self._save_acc()
        return created

    def set_auth(self, uid: int, auth: bool) -> None:
        rec = self._acc.setdefault(str(uid), {"joined": time.time(), "tokens": 0})
        rec["auth"], rec["blocked"] = bool(auth), not auth
        self._save_acc()

    def is_authorized(self, uid: int) -> bool:
        return bool(self.account(uid)["auth"])

    def is_blocked(self, uid: int) -> bool:
        return bool(self.account(uid)["blocked"])

    def tokens(self, uid: int) -> int:
        return int(self.account(uid)["tokens"])

    def add_tokens(self, uid: int, n: int) -> int:
        rec = self._acc.setdefault(str(uid), {"joined": time.time(), "tokens": 0})
        rec["tokens"] = max(0, int(rec.get("tokens", 0)) + int(n))
        self._save_acc()
        return rec["tokens"]

    def spend_tokens(self, uid: int, n: int) -> bool:
        if self.tokens(uid) < n:
            return False
        self.add_tokens(uid, -n)
        return True

    def register_referral(self, new_uid: int, ref_uid: int, bonus: int) -> bool:
        """Crédite le parrain pour un NOUVEL ami (une seule fois par ami, jamais soi-même)."""
        if new_uid == ref_uid:
            return False
        rec = self._acc.setdefault(str(new_uid), {"joined": time.time(), "tokens": 0})
        if rec.get("ref_by") is not None:
            return False
        rec["ref_by"] = ref_uid
        parent = self._acc.setdefault(str(ref_uid), {"joined": time.time(), "tokens": 0})
        parent.setdefault("invited", []).append(new_uid)
        parent["ref_earned"] = int(parent.get("ref_earned", 0)) + int(bonus)
        parent["tokens"] = int(parent.get("tokens", 0)) + int(bonus)
        self._save_acc()
        return True

    def claim_channel(self, uid: int, bonus: int) -> bool:
        """Bonus de vérification du canal : une seule fois par compte."""
        rec = self._acc.setdefault(str(uid), {"joined": time.time(), "tokens": 0})
        if rec.get("channel_ok"):
            return False
        rec["channel_ok"] = True
        rec["tokens"] = int(rec.get("tokens", 0)) + int(bonus)
        self._save_acc()
        return True

    def accounts(self) -> Dict[int, dict]:
        return {int(k): self.account(int(k)) for k in self._acc}

    # ---- vérification d'affiliation (ID Pocket Option), consentement, bonus, dépôts -----------------
    def po_owner(self, po_id: str) -> Optional[int]:
        """Compte Telegram déjà lié à cet ID Pocket Option (un ID ne sert qu'à un seul compte)."""
        for k, rec in self._acc.items():
            if po_id and str(rec.get("po_id", "")) == str(po_id):
                return int(k)
        return None

    def set_verification(self, uid: int, status: str, po_id: Optional[str] = None, source: str = "") -> None:
        """status : none | pending | ok | rejected."""
        rec = self._acc.setdefault(str(uid), {"joined": time.time(), "tokens": 0})
        rec["verify_status"] = status
        rec["verified"] = status == "ok"
        rec["verify_at"] = time.time()
        if po_id is not None:
            rec["po_id"] = str(po_id)
        if source:
            rec["verify_source"] = source
        self._save_acc()

    def set_consent(self, uid: int) -> None:
        rec = self._acc.setdefault(str(uid), {"joined": time.time(), "tokens": 0})
        rec["consent"] = time.time()
        self._save_acc()

    def claim_bonus(self, uid: int, tokens: int, hours: int):
        """Bonus récurrent : retourne (accordé, secondes avant le prochain)."""
        rec = self._acc.setdefault(str(uid), {"joined": time.time(), "tokens": 0})
        last = float(rec.get("bonus_at", 0))
        if last and (hours <= 0 or time.time() - last < hours * 3600):
            return False, (0 if hours <= 0 else int(hours * 3600 - (time.time() - last)))
        rec["bonus_at"] = time.time()
        rec["tokens"] = int(rec.get("tokens", 0)) + int(tokens)
        self._save_acc()
        return True, 0

    def add_dep_credited(self, uid: int, amount: float) -> float:
        rec = self._acc.setdefault(str(uid), {"joined": time.time(), "tokens": 0})
        rec["dep_credited"] = round(float(rec.get("dep_credited", 0)) + float(amount), 2)
        self._save_acc()
        return rec["dep_credited"]

    def pending_verifications(self) -> List[int]:
        return [int(k) for k, r in self._acc.items() if r.get("verify_status") == "pending"]

    def find_accounts(self, query: str) -> List[int]:
        """Recherche par ID Telegram, @pseudo, nom ou ID Pocket Option."""
        q = query.strip().lstrip("@").lower()
        out = []
        for k, r in self._acc.items():
            if q in (k, str(r.get("po_id", "")).lower()) or (q and (q in str(r.get("username", "")).lower()
                                                                   or q in str(r.get("name", "")).lower())):
                out.append(int(k))
        return out

    # ---- sessions en cours (pour prévenir les utilisateurs si le bot redémarre) ------------------
    def mark_session(self, uid: int, kind: Optional[str], chat_id: Optional[int] = None) -> None:
        sess = self._read(self.dir / "sessions.json")
        if kind:
            sess[str(uid)] = {"kind": kind, "chat": chat_id or uid, "since": time.time()}
        else:
            sess.pop(str(uid), None)
        self._write(self.dir / "sessions.json", sess)

    def interrupted_sessions(self) -> Dict[int, dict]:
        """Sessions démarrées avant l'arrêt du bot et jamais terminées proprement."""
        return {int(k): v for k, v in self._read(self.dir / "sessions.json").items()}

    def clear_sessions(self) -> None:
        self._write(self.dir / "sessions.json", {})

