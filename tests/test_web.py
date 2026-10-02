"""Automatisation Playwright testée contre de FAUX sites locaux (aucun accès à Pocket Option)."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import po_web as W  # noqa: E402
import ssid as ssidlib  # noqa: E402
from config import Config  # noqa: E402
from tests import mock_sites as M  # noqa: E402


# ---------------------------------------------------------------------------- fonctions pures
def test_pure_helpers():
    assert W.valid_email("jean.dupont+x@mail.co.uk") and not W.valid_email("jean@") and not W.valid_email("a b@c.de")
    assert W.mask_email("jean.dupont@mail.com") == "je***@mail.com"
    for _ in range(50):
        p = W.strong_password()
        assert len(p) == 14 and sum(c.isupper() for c in p) >= 2 and sum(c.islower() for c in p) >= 2 \
            and sum(c.isdigit() for c in p) >= 2 and not set(p) & set("lIO01"), p
    assert len({W.strong_password() for _ in range(20)}) == 20
    assert W.normalize_trader_id("ID: 12 345 678") == "12345678" and W.normalize_trader_id("abc") == "" \
        and W.normalize_trader_id("123") == ""
    assert W.extract_trader_id('<div>ID: 87654321</div>') == "87654321"
    assert W.extract_trader_id('var x = {"uid": 55555555, "a":1}') == "55555555"
    assert W.extract_trader_id("User ID 4444444") == "4444444" and W.extract_trader_id("rien") == ""
    base = dict(text="", error_text="", has_session=False, on_form=True)
    c = W.classify_signup
    assert c(base) is None
    assert c({**base, "text": "already have an account? log in"}) is None, "texte statique : pas une erreur"
    assert c({**base, "error_text": "this email is already registered"}) == "exists"
    assert c({**base, "error_text": "invalid email address"}) == "invalid_email"
    assert c({**base, "error_text": "you must accept the terms"}) == "form_error"
    assert c({**base, "on_form": False, "has_session": True}) == "ok"
    assert c({**base, "on_form": False}) == "created_nosession"
    assert c({**base, "on_form": False, "text": "please check your email: verification link"}) == "confirm_email"
    assert W.classify_login({**base, "error_text": "invalid email or password"}) == "bad_credentials"
    assert W.classify_login({**base, "on_form": False, "text": "enter the verification code"}) == "twofa"
    tables = [{"headers": ["Trader ID", "Deposits count", "Deposits sum ($)"],
               "rows": [["111111111", "1", "$10.00"], ["11111111", "2", "$1,250.50"], ["22222222", "0", "0"]]}]
    assert W.find_trader(tables, "11111111") == (True, 1250.5), "ID exact (pas 111111111) + colonne somme"
    assert W.find_trader(tables, "2222222") == (False, None)
    assert W.find_trader([{"headers": [], "rows": [["#555555", "x"]]}], "555555") == (True, None)
    assert W._num("1 250,75 €") == 1250.75 and W._num("$1,250.50") == 1250.5 and W._num("n/a") is None
    packs = ((20, 200), (50, 600), (100, 1500))
    assert W.tokens_for(packs, 50) == 600 and W.tokens_for(packs, 75) == 900 and W.tokens_for(packs, 10) == 0
    assert W.load_selectors("/inexistant")["register"]["email"][0] == 'input[name="email"]'
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "s.json"
        f.write_text(json.dumps({"register": {"email": "#mon-email"}}), encoding="utf-8")
        assert W.load_selectors(str(f))["register"]["email"] == ["#mon-email"]
        assert W.load_selectors(str(f))["register"]["password"], "les autres clés restent"
    # SSID depuis le cookie
    sess = M.session_for("a@b.c")
    s, err = ssidlib.from_cookie(sess, 12345678, demo=True)
    assert err == "" and ssidlib.parse_ssid(s) == (s, True, "") and ssidlib.po_uid(s) == 12345678
    from urllib.parse import quote
    assert ssidlib.looks_like_cookie(quote(sess, safe="")) and ssidlib.looks_like_cookie(sess)
    assert ssidlib.from_cookie(quote(sess, safe=""), 12345678, False)[0].count('"isDemo":0') == 1
    assert ssidlib.from_cookie(sess, None, True)[1] and not ssidlib.looks_like_cookie("bonjour")
    print("✓ fonctions pures (e-mail, mot de passe, ID, classification, tableaux, SSID depuis cookie)")


# ---------------------------------------------------------------------------- navigateur réel
def make(base, tmp, **kw):
    cfg = Config("x", frozenset(), Path(tmp), "pocketoption", False, 20.0, "", "",
                 po_register_url=base + "/register", po_login_url=base + "/login", po_profile_url=base + "/cabinet/profile/",
                 partners_login_url=base + "/partners/login", partners_stats_url=base + "/partners/stats",
                 partners_email="p@x.com", partners_password="pw", web_timeout=kw.pop("timeout", 10), web_concurrency=2, **kw)
    web = W.WebAutomation(cfg, Path(tmp))
    return cfg, web, W.PartnerChecker(cfg, web)


async def browser_tests():
    srv, base = M.start()
    with tempfile.TemporaryDirectory() as tmp:
        cfg, web, partners = make(base, tmp)
        ok, detail = await web.diagnose()
        if not ok:
            print("SKIP navigateur :", detail)
            return
        # ---- inscription
        r = await web.signup("jean@mail.com", "Abcd1234Efgh", True)
        assert r.status == "ok" and r.trader_id == M.trader_id_for("jean@mail.com"), (r.status, r.detail)
        assert r.session == M.session_for("jean@mail.com") and r.submitted
        s = r.ssid(demo=True)
        assert ssidlib.parse_ssid(s)[1:] == (True, "") and ssidlib.po_uid(s) == int(r.trader_id)
        assert r.ssid(False).count('"isDemo":0') == 1
        r = await web.signup("exists@mail.com", "Abcd1234Efgh", True)
        assert r.status == "exists", (r.status, r.detail)                     # malgré « Already have an account? »
        assert (await web.signup("bademail@mail.com", "Abcd1234Efgh", True)).status == "invalid_email"
        r = await web.signup("confirm@mail.com", "Abcd1234Efgh", True)
        assert r.status == "confirm_email" and r.submitted
        r = await web.signup("nocookie@mail.com", "Abcd1234Efgh", True)
        assert r.status == "created_nosession" and r.session == "" and r.submitted
        n = srv.counts["register_post"]
        t0 = time.time()
        r = await web.signup("slow@mail.com", "Abcd1234Efgh", True)           # réponse lente : on attend, pas d'erreur
        assert r.status == "ok" and srv.counts["register_post"] == n + 1, r.status
        assert (await web.signup("x@mail.com", "Abcd1234Efgh", False)).status == "error", "sans consentement : rien n'est envoyé"
        assert srv.counts["register_post"] == n + 1
        # CAPTCHA : détecté, jamais contourné
        cfg2, web2, _ = make(base, tmp, timeout=5)
        r = await web2.signup("captcha@mail.com", "Abcd1234Efgh", True)
        assert r.status == "captcha" and r.diag and Path(r.diag).exists(), (r.status, r.detail)
        print("✓ inscription : ok / déjà inscrit / e-mail invalide / confirmation / sans cookie / lente / CAPTCHA détecté")
        # variante avec confirmation du mot de passe + sélecteurs personnalisés
        f = Path(tmp) / "sel.json"
        f.write_text(json.dumps({"register": {"email": ["input[name=email]"]}}), encoding="utf-8")
        cfg3, web3, _ = make(base, tmp, selectors_file=str(f))
        cfg3 = Config(**{**cfg3.__dict__, "po_register_url": base + "/register?variant=confirm"})
        web3.cfg = cfg3
        assert (await web3.signup("conf@mail.com", "Abcd1234Efgh", True)).status == "ok"
        # ---- connexion
        r = await web.login("paul@mail.com", "Abcd1234Efgh")
        assert r.status == "ok" and r.trader_id == M.trader_id_for("paul@mail.com") and r.session == M.session_for("paul@mail.com")
        r = await web.login("noid@mail.com", "Abcd1234Efgh")                   # ID absent de la page : lu sur le profil
        assert r.status == "ok" and r.trader_id == M.trader_id_for("noid@mail.com"), (r.status, r.trader_id)
        assert (await web.login("paul@mail.com", "wrong")).status == "bad_credentials"
        assert (await web.login("2fa@mail.com", "Abcd1234Efgh")).status == "twofa"
        assert (await web.login("confirm@mail.com", "Abcd1234Efgh")).status == "confirm_email"
        print("✓ connexion : ok / ID via profil / mauvais mot de passe / 2FA / confirmation e-mail")
        # ---- espace partenaire
        r = await partners.check("11111111")
        assert r.status == "found" and r.deposits == 1250.5, (r.status, r.detail)
        assert srv.counts["partner_login"] == 1
        r = await partners.check("33333333", fresh=True)                         # page 2 (pagination)
        assert r.status == "found" and r.deposits == 75.0
        assert srv.counts["partner_login"] == 1, "la session partenaire est réutilisée (pas de reconnexion)"
        assert (await partners.check("999999999")).status == "not_found"
        assert (await partners.check("1111111")).status == "not_found", "pas de correspondance partielle"
        t0 = time.time()
        await partners.check("999999999")
        assert time.time() - t0 < 1, "résultat négatif mis en cache"
        cfgb, webb, partb = make(base, tempfile.mkdtemp())
        partb.cfg = Config(**{**cfgb.__dict__, "partners_password": "badpartner"})
        r = await partb.check("11111111")
        assert r.status == "config_error", (r.status, r.detail)
        assert (await W.PartnerChecker(Config(**{**cfg.__dict__, "partners_email": ""}), web).check("1")).status == "config_error"
        print("✓ partenaire : trouvé + dépôts / page 2 / session réutilisée / introuvable / cache / identifiants refusés")
        # ---- robustesse réseau
        cfgd, webd, _ = make("http://127.0.0.1:9", tmp, timeout=3)               # port fermé
        t0 = time.time()
        r = await webd.signup("a@b.co", "Abcd1234Efgh", True)
        assert r.status == "error" and not r.submitted and "injoignable" in r.detail.lower() or r.status == "error", (r.status, r.detail)
        assert time.time() - t0 < 40
        r = await webd.login("a@b.co", "x")
        assert r.status == "error"
        r = await W.PartnerChecker(cfgd, webd).check("11111111")
        assert r.status in ("error", "unavailable")
        print("✓ site injoignable : erreur propre, pas de plantage, pas de navigateur bloqué")
        # non configuré / Playwright absent
        nocfg = W.WebAutomation(Config(**{**cfg.__dict__, "po_register_url": ""}), Path(tmp))
        assert (await nocfg.signup("a@b.co", "x", True)).status == "unavailable"
        webx = W.WebAutomation(cfg, Path(tmp))
        webx.available = lambda: False
        assert (await webx.signup("a@b.co", "x", True)).status == "unavailable"
        assert (await webx.login("a@b.co", "x")).status == "unavailable"
        assert (await W.PartnerChecker(cfg, webx).check("1")).status == "unavailable"
        # navigateur fermé et pas de tâche en attente
        await asyncio.sleep(0.3)
        left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert not left, left
    srv.shutdown()


if __name__ == "__main__":
    test_pure_helpers()
    asyncio.run(browser_tests())
    print("web OK")
