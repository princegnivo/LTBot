"""SSID : lecture tolérante des formats collés + stockage robuste (copie .bak, fichier corrompu)."""
import json
import tempfile
from pathlib import Path

import ssid
from store import Store

GOOD = ('42["auth",{"session":"a:4:{s:10:\\"session_id\\";s:32:\\"abc123def456\\";}xyz789","isDemo":1,'
        '"uid":12345678,"platform":2,"isFastHistory":true}]')


def test_pasted_formats_are_all_read():
    variants = {
        "tel quel": GOOD,
        "bloc de code + texte": "Voici mon ssid :\n```\n" + GOOD + "\n```\nmerci",
        "guillemets iPhone": GOOD.replace('"', "”").replace("\\”", '\\"'),
        "sauts de ligne": GOOD[:30] + "\n" + GOOD[30:],
        "entouré de guillemets": json.dumps(GOOD),
        "antislashs perdus": GOOD.replace('\\"', '"'),
        "sans le 42": GOOD[2:],
        "caractères invisibles": GOOD[:20] + "\u200b" + GOOD[20:],
        "espaces insécables": "\u00a0" + GOOD + "\u00a0",
    }
    for name, raw in variants.items():
        s, demo, err = ssid.parse_ssid(raw)
        assert err == "" and demo is True and ssid.po_uid(s) == 12345678, (name, err)
        assert s == GOOD, name
    s, demo, err = ssid.parse_ssid(GOOD.replace('"isDemo":1', '"isDemo":0'))
    assert err == "" and demo is False


def test_bad_inputs_are_explained():
    assert "42[" in ssid.parse_ssid("bonjour")[2]
    assert "coupé" in ssid.parse_ssid(GOOD[:-12])[2]
    assert "trop long" in ssid.parse_ssid("x" * 5000)[2]
    assert ssid.parse_ssid('42["auth",{"session":"abc"}]')[2], "session trop courte"
    assert ssid.parse_ssid("")[2]


def test_missing_isdemo_is_reported_as_unknown():
    s, demo, err = ssid.parse_ssid('42["auth",{"session":"abcdefghijkl","uid":5,"platform":2}]')
    assert err == "" and demo is None


def test_store_survives_corruption_and_keeps_a_backup():
    with tempfile.TemporaryDirectory() as tmp:
        st = Store(tmp)
        st.set_ssid(7, "demo", GOOD)
        st.set_ssid(7, "real", GOOD.replace('"isDemo":1', '"isDemo":0'))     # 2e écriture -> .bak du 1er état
        st.set_ssid_meta(7, "demo", saved=1.0, verified=2.0, error="", po_uid=12345678)
        assert (Path(tmp) / "secrets.json.bak").exists()
        assert oct((Path(tmp) / "secrets.json").stat().st_mode)[-3:] == "600", "fichier privé"
        # corruption du fichier principal : la copie .bak est relue, le fichier abîmé est mis de côté (jamais écrasé)
        (Path(tmp) / "secrets.json").write_text("{pas du json", encoding="utf-8")
        st2 = Store(tmp)
        assert st2.get_ssid(7, "demo") == GOOD
        assert any(p.name.startswith("secrets.json.corrompu-") for p in Path(tmp).iterdir())
        assert st2.ssid_meta(7, "demo")["po_uid"] == 12345678
        st2.del_ssid(7, "demo")
        assert st2.get_ssid(7, "demo") == "" and st2.ssid_meta(7, "demo") == {}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("✓", n)
