"""Textes modifiables, réglages et images modifiés depuis Telegram (admin) : fichiers, validation, application immédiate."""
import asyncio
import io
import json
import sys
import tempfile
from pathlib import Path

import tests.test_bot_flow as BF  # noqa: F401  (faux module « telegram » si la vraie bibliothèque est absente)
from tests.test_bot_flow import ADMIN, USER, World

import runtime_settings as RS  # noqa: E402
import texts  # noqa: E402
import visuals  # noqa: E402
from PIL import Image  # noqa: E402


def test_texts_override_validation_and_hot_reload():
    with tempfile.TemporaryDirectory() as d:
        texts.configure(d)
        assert texts.get("deposit_after") == texts.REGISTRY["deposit_after"][2], "sans fichier : texte d'origine"
        assert texts.check("tokens", "Vous avez {tokens} jetons") is None
        assert "inconnue" in texts.check("tokens", "Vous avez {solde} jetons")
        assert "HTML" in texts.check("deposit", "<b>oups")
        assert "HTML" in texts.check("deposit", "<script>x</script>")
        assert texts.check("faq", "n'importe quoi < > {") is None, "FAQ : texte simple"
        texts.save("tokens", "💎 {tokens} jetons (coût {cost_real}/{cost_demo})")
        assert texts.get("tokens", cost_real=2, cost_demo=1, tokens=7) == "💎 7 jetons (coût 2/1)"
        # modification à la main du fichier : prise en compte sans redémarrage
        p = Path(d) / "textes.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["tokens"] = "Solde : {tokens}"
        p.write_text(json.dumps(data), encoding="utf-8")
        import os, time
        os.utime(p, (time.time() + 5, time.time() + 5))
        assert texts.get("tokens", cost_real=2, cost_demo=1, tokens=9) == "Solde : 9"
        # fichier cassé ou texte invalide : jamais d'erreur, texte d'origine
        p.write_text(json.dumps({"tokens": "{inconnue}"}), encoding="utf-8")
        os.utime(p, (time.time() + 10, time.time() + 10))
        assert "Jetons" in texts.get("tokens", cost_real=2, cost_demo=1, tokens=1)
        p.write_text("pas du json", encoding="utf-8")
        os.utime(p, (time.time() + 15, time.time() + 15))
        assert "Jetons" in texts.get("tokens", cost_real=2, cost_demo=1, tokens=1)
        texts.save("tokens", "")                                       # rétablir
        assert texts.override("tokens") is None
    ex = json.loads(texts.example())
    assert set(texts.REGISTRY) <= set(ex) and ex["faq"] == "", "le modèle ne remplace pas la FAQ"
    texts.configure(None) if False else None


def test_runtime_settings_parse_apply_and_persist():
    with tempfile.TemporaryDirectory() as d:
        cfg = BF.dataclasses.replace(World(d).cfg) if hasattr(World(d), "cfg") else None
        assert RS.parse("bonus_tokens", "12") == 12
        for bad in ("abc", "-3", "999999"):
            try:
                RS.parse("bonus_tokens", bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        assert RS.parse("deposit_packs", "50:600, 20:200") == ((20, 200), (50, 600))
        for bad in ("20", "a:b", ""):
            try:
                RS.parse("deposit_packs", bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        assert RS.parse("support_url", "@monsupport") == "https://t.me/monsupport"
        assert RS.parse("image_handle", "LegitTradeAI_bot") == "@LegitTradeAI_bot"
        assert RS.parse("promo_code", "-") == ""
        assert RS.parse("auto_signup", "Non") is False and RS.parse("require_verified", "oui") is True
        assert RS.parse("verify_mode", "AUTO") == "auto" and RS.parse("web_timeout", "90") == 90
        assert RS.parse("deposit_url", "https://x.com/d?amount={amount}") == "https://x.com/d?amount={amount}"
        for key, bad in (("auto_signup", "peut-être"), ("verify_mode", "demain"), ("web_timeout", "5"), ("web_timeout", "999"),
                         ("deposit_url", "https://x.com/d"), ("deposit_url", "https://x.com/{amount}{autre}"),
                         ("po_login_url", "-")):
            try:
                RS.parse(key, bad)
                raise AssertionError((key, bad))
            except ValueError:
                pass
        for key, bad in (("channel_id", "nom"), ("support_url", "monsupport.com"), ("bot_name", "-")):
            try:
                RS.parse(key, bad)
                raise AssertionError(key)
            except ValueError:
                pass
        w = World(d)
        RS.set_value(w.cfg, "promo_code", "TEST50")
        RS.set_value(w.cfg, "partners_password", "s3cret!")
        RS.set_value(w.cfg, "deposit_packs", "10:100")
        RS.set_value(w.cfg, "auto_signup", "non")
        assert w.cfg.auto_signup is False and RS.display(w.cfg, "auto_signup") == "❌ non"
        assert w.cfg.promo_code == "TEST50" and w.cfg.deposit_packs == ((10, 100),), "appliqué tout de suite (config figée contournée)"
        assert RS.display(w.cfg, "partners_password") == "••••••••", "secret masqué"
        saved = json.loads((Path(d) / "reglages.json").read_text(encoding="utf-8"))
        assert saved["promo_code"] == "TEST50" and saved["deposit_packs"] == [[10, 100]]
        w2 = World(tempfile.mkdtemp())                                 # « redémarrage » : on recharge depuis le fichier
        RS.apply(w2.cfg, RS.load(d))
        assert w2.cfg.promo_code == "TEST50" and w2.cfg.deposit_packs == ((10, 100),) and w2.cfg.partners_password == "s3cret!"


def test_custom_banner_saved_normalized_and_preferred():
    with tempfile.TemporaryDirectory() as d:
        visuals.configure("")
        visuals.set_custom_dir(Path(d) / "banners")
        shipped = visuals.banner_path("menu")
        buf = io.BytesIO()
        Image.new("RGB", (300, 900), (200, 30, 30)).save(buf, "PNG")
        out = visuals.save_custom_banner("menu", buf.getvalue())
        assert Image.open(out).size == (800, 384), "même format pour toutes les images d'écran"
        assert visuals.banner_path("menu") == out != shipped, "l'image de l'admin prime sur celle d'origine"
        try:
            visuals.save_custom_banner("menu", b"pas une image")
            raise AssertionError("image invalide acceptée")
        except ValueError:
            pass
        try:
            visuals.save_custom_banner("inconnu", buf.getvalue())
            raise AssertionError("écran inconnu accepté")
        except ValueError:
            pass
        assert visuals.delete_custom_banner("menu") and visuals.banner_path("menu") == shipped, "suppression : l'image d'origine revient"
        visuals.set_custom_dir(None)


def test_admin_flows_in_telegram():
    async def run():
        with tempfile.TemporaryDirectory() as d:
            texts.configure(d)
            visuals.set_custom_dir(Path(d) / "banners")
            w = World(d)
            # --- réglage : touche, saisie valide, saisie invalide
            await w.tap(ADMIN, "adm:cfg")
            await w.tap(ADMIN, "adm:cfg:bonus_tokens")
            await w.say(ADMIN, "abc")
            assert w.ctx(ADMIN).user_data.get("await") == ("adm_cfg", "bonus_tokens"), "saisie invalide : on redemande"
            await w.say(ADMIN, "8")
            assert w.cfg.bonus_tokens == 8 and not w.ctx(ADMIN).user_data.get("await")
            assert json.loads((Path(d) / "reglages.json").read_text())["bonus_tokens"] == 8
            # un utilisateur normal ne peut rien changer
            await w.tap(USER, "adm:cfg:bonus_tokens")
            await w.say(USER, "999")
            assert w.cfg.bonus_tokens == 8
            # --- texte : modification, invalide, rétablir
            await w.tap(ADMIN, "adm:txt:deposit_after:edit")
            await w.say(ADMIN, "Après le dépôt, touchez le bouton « J'ai déposé ».")
            import bot.accounts as AC
            assert "touchez le bouton" in texts.get("deposit_after")
            await w.tap(ADMIN, "adm:txt:tokens:edit")
            await w.say(ADMIN, "Solde {pas_une_variable}")
            assert w.ctx(ADMIN).user_data.get("await") == ("adm_txt", "tokens"), "variable inconnue : refusé"
            assert texts.override("tokens") is None
            await w.tap(ADMIN, "adm:txt:deposit_after:reset")
            assert texts.override("deposit_after") is None
            # --- images : écran d'envoi puis photo (via le gestionnaire)
            await w.tap(ADMIN, "adm:img:menu")
            assert w.ctx(ADMIN).user_data.get("await") == ("adm_img", "menu")
            buf = io.BytesIO()
            Image.new("RGB", (1000, 400), (10, 80, 10)).save(buf, "JPEG")

            class FakeFile:
                async def download_as_bytearray(self):
                    return bytearray(buf.getvalue())

            class Photo:
                async def get_file(self):
                    return FakeFile()

            up = w.upd(ADMIN)
            up.effective_message.photo = [Photo()]
            up.effective_message.document = None
            import bot.app as A
            await A.on_photo(up, w.ctx(ADMIN))
            assert Image.open(visuals.banner_path("menu")).size == (800, 384)
            assert str(visuals.banner_path("menu")).startswith(d), "enregistrée dans DATA_DIR (conservée à l'hébergement)"
            assert not w.ctx(ADMIN).user_data.get("await")
        visuals.set_custom_dir(None)
    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
    print("admin/config OK")
