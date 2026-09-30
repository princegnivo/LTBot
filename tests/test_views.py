"""Format des signaux (demandé par l'utilisateur) et migration des anciens réglages."""
import json
import tempfile
import time

import views
from store import Store
from strategies.signal import Signal


def test_signal_format():
    sig = Signal("2M", "EURCHF_otc", "call", 80, 120,
                 "Croisement MACD haussier (il y a 0 bougie(s)) + proximité bande Bollinger + bougie forte et stable", 1.0,
                 created_at=time.mktime((2026, 9, 29, 17, 44, 0, 0, 0, -1)))
    out = views.signal_text({"sig": sig, "name": "EUR/CHF OTC"})
    lines = out.split("\n")
    assert lines[0] == "_" * 30 and lines[-1] == "—" * 19
    assert lines[1] == "📊 <b>ACTIF:</b> 🇪🇺 EUR/CHF 🇨🇭 OTC"
    assert lines[2] == "🕘 <b>HEURE D'ENTRÉE:</b> 17:44"
    assert lines[3] == "⏳ <b>EXPIRATION:</b> 120s (2min)"
    assert lines[5] == "🔮 Direction: <b>ACHAT</b>"
    assert lines[7] == "🔘<b>INFO:</b>" and lines[8].startswith("🧠")
    put = views.signal_text({"sig": Signal("5s", "AEDCNY_otc", "put", 80, 5, "x", 1.0), "name": ""})
    assert "<b>VENTE</b>" in put and "<b>EXPIRATION:</b> 5s\n" in put and "🇦🇪 AED/CNY 🇨🇳 OTC" in put


def test_migration_old_settings():
    with tempfile.TemporaryDirectory() as tmp:
        (__import__("pathlib").Path(tmp) / "users.json").write_text(json.dumps(
            {"7": {"min_payout": 80, "scan_assets": 5, "stake": 3.0, "fast_mode": True}}))
        s = Store(tmp).settings(7)
        assert s.min_payout == 92 and s.scan_assets == 2 and s.stake == 3.0 and s.currencies_only


if __name__ == "__main__":
    test_signal_format(); test_migration_old_settings(); print("✓ views/store")
