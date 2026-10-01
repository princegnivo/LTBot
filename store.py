"""Persistance JSON : réglages par utilisateur, SSID (chmod 600) et journal des trades."""
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List


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

    # ---- io atomique --------------------------------------------------------
    @staticmethod
    def _read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write(path: Path, data: dict, private: bool = False) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        if private:
            os.chmod(tmp, 0o600)
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
                    invited=[], ref_earned=0, channel_ok=False)
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
