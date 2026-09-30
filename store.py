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
    strategies: List[str] = field(default_factory=lambda: ["1M", "2M", "5s"])
    min_confidence: int = 60
    speed: str = "ultra"               # "ultra" | "rapide" | "normal" (bougie en formation) | "cloture" (à la clôture)
    trend_filter: bool = False         # refuse les signaux à contre-tendance (EMA9/EMA21 + MACD)
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
    max_consec_losses: int = 3         # deals perdus d'affilée avant arrêt (0 = désactivé)
    # martingale
    mg_enabled: bool = True
    mg_steps: int = 2                  # étapes supplémentaires après la mise initiale (1M et 5s)
    mg_method: str = "recovery"        # "recovery" (perte cumulée + gain visé) | "multiplier"
    mg_multiplier: float = 2.2
    # positions multiples & risque
    max_open: int = 3                  # deals simultanés
    max_open_per_asset: int = 1
    max_exposure_pct: float = 30.0     # somme des mises ouvertes / solde
    max_step_pct: float = 25.0         # une étape de martingale ne peut pas dépasser x % du solde
    cooldown_s: int = 3                # délai mini entre deux entrées sur un même actif
    # manuel
    manual_exp: int = 60
    # notifications
    notify_signals: bool = True
    notify_results: bool = True


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._users_path = self.dir / "users.json"
        self._secrets_path = self.dir / "secrets.json"
        self._trades_path = self.dir / "trades.jsonl"
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
        known = {f.name for f in fields(UserSettings)}
        return UserSettings(**{k: v for k, v in raw.items() if k in known})

    def save_settings(self, uid: int, s: UserSettings) -> None:
        self._users[str(uid)] = {**asdict(s), "_v": 2}
        self._write(self._users_path, self._users)

    # ---- SSID ---------------------------------------------------------------
    def set_ssid(self, uid: int, mode: str, ssid: str) -> None:
        self._secrets.setdefault(str(uid), {})[mode] = ssid
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
