"""Parcours d'arrivée complet : vrai Chromium (Playwright) contre de faux sites locaux + faux Telegram."""
import asyncio
import dataclasses
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import mock_sites as M  # noqa: E402
from tests.test_bot_flow import ADMIN, World, cbs, labels, urls  # noqa: E402
import ssid as ssidlib  # noqa: E402
import po_web as W  # noqa: E402
from bot import onboarding as ONB  # noqa: E402

PW = "Abcd1234Efgh"


def texts(w, uid):
    return [m for m in w.bot.sent if m.chat == uid]


def find(w, uid, needle):
    return [m for m in texts(w, uid) if needle in m.text]


def datadir_has(tmp, needle: str) -> bool:
    for p in Path(tmp).rglob("*"):
        if p.is_file() and p.suffix in (".json", ".bak", ".jsonl", ".txt"):
            try:
                if needle in p.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except OSError:
                pass
    return False


async def run():
    srv, base = M.start()
    with tempfile.TemporaryDirectory() as tmp:
        w = World(tmp, require_verified=True, verify_mode="auto", web_timeout=8,
                  po_register_url=base + "/register?code=50START", po_login_url=base + "/login",
                  po_profile_url=base + "/cabinet/profile/", partners_login_url=base + "/partners/login",
                  partners_stats_url=base + "/partners/stats", partners_email="p@x.com", partners_password="pw")
        ok, detail = await w.app.web.diagnose()
        if not ok:
            print("SKIP navigateur :", detail)
            return
        ONB.PW_TTL = 1.5
        U1, U2, U3, U4, U5, U6, U7, U8, U9, U10 = range(201, 211)

        # ============================================================ accueil + garde
        await w.start(U1)
        wel = w.last(U1)
        assert "Je suis LEGIT AI" in wel.text and "monde des millionnaires" in wel.text and "risque de perte" in wel.text, wel.text
        assert cbs(wel) == ["onb:reg", "onb:have"] and labels(wel)[:2] == ["INSCRIPTION", "DÉJÀ INSCRIT"]
        assert "https://t.me/support" in urls(wel)
        assert not find(w, U1, "⌨️"), "pas de clavier permanent avant l'inscription"
        for head in ("menu", "auto", "man", "tok", "dep", "set", "bonus"):
            msg, _ = await w.tap(U1, head)
            assert "Je suis LEGIT AI" in msg.text, head                # tout ramène à l'accueil
        msg, _ = await w.tap(ADMIN, "menu")
        assert "Administration" in " ".join(labels(msg)), "l'admin n'est jamais bloqué"
        msg, _ = await w.tap(U1, "onb:reg")
        assert cbs(msg)[0] == "onb:new" and labels(msg)[0] == "TE CRÉER UN COMPTE"
        assert any(u.startswith(base + "/register") and "code=50START" in u for u in urls(msg))
        print("✓ accueil, garde d'accès, INSCRIPTION → TE CRÉER UN COMPTE / S'INSCRIRE")

        # ============================================================ création automatique (vrai navigateur)
        msg, _ = await w.tap(U1, "onb:new", msg)
        assert "18 ans" in msg.text and "onb:consent" in cbs(msg) and srv.counts["register_post"] == 0
        msg, _ = await w.tap(U1, "onb:consent", msg)
        assert "Entrez votre e-mail" in msg.text and "créer un compte de trading" in msg.text
        await w.say(U1, "pas un email")
        assert w.data[U1].get("await") == ("onb_email",) and "invalide" in msg.text
        await w.say(U1, "jean@mail.com")
        assert w.app.store.account(U1)["verified"] and w.app.store.account(U1)["po_id"] == M.trader_id_for("jean@mail.com")
        assert w.app.store.account(U1)["verify_source"] == "bot"
        assert w.store.get_ssid(U1, "demo") and w.store.get_ssid(U1, "real")
        assert ssidlib.po_uid(w.store.get_ssid(U1, "demo")) == int(M.trader_id_for("jean@mail.com"))
        cred = find(w, U1, "Mot de passe")[0]
        pwd = re.search(r"Mot de passe : (\S+)", cred.text).group(1)
        pwd = re.sub(r"</?code>", "", pwd)
        assert len(pwd) == 14 and "jean@mail.com" in cred.text and "Note-les" in cred.text
        assert "SSID démo enregistré" in cred.text and cbs(cred) == ["menu"]
        assert not [m for m in find(w, U1, "Création de ton compte") if not m.deleted], "message « en cours » supprimé"
        assert any(m.text == "⌨️" and m.deleted for m in texts(w, U1)), "clavier permanent installé"
        assert not datadir_has(tmp, pwd), "le mot de passe n'est jamais stocké"
        assert not cred.deleted
        await asyncio.sleep(2.2)
        assert cred.deleted, "le message contenant le mot de passe est supprimé après 10 min (1,5 s en test)"
        msg, _ = await w.tap(U1, "menu")
        assert "Jetons :" in msg.text, "menu accessible après inscription"
        print("✓ TE CRÉER UN COMPTE : compte créé, SSID démo+réel, mot de passe affiché puis supprimé et jamais stocké")

        # ============================================================ cas d'erreur de la création
        async def signup_flow(uid, email, consent=True):
            await w.start(uid)
            await w.tap(uid, "onb:new")
            if consent:
                await w.tap(uid, "onb:consent")
            else:
                w.store.ensure_account(uid)
                w.data[uid]["await"] = ("onb_email",)
            await w.say(uid, email)

        await signup_flow(U2, "exists@mail.com")
        assert "déjà" in w.last(U2).text and "onb:have" in cbs(w.last(U2)) and not w.store.account(U2)["verified"]
        n = srv.counts["register_post"]
        await signup_flow(U3, "x@mail.com", consent=False)                          # sans consentement : rien n'est envoyé
        assert srv.counts["register_post"] == n and "impossible" in w.last(U3).text
        assert "onb:have" in cbs(w.last(U3)) and any("example" in u or "register" in u for u in urls(w.last(U3)))
        await signup_flow(U4, "captcha@mail.com")
        assert "anti-robot" in w.last(U4).text and "Réessayer" in " ".join(labels(w.last(U4)))
        assert any("Inscription automatique en échec" in m.text and "captcha" in m.text for m in texts(w, ADMIN))
        assert w.bot.photos, "capture de diagnostic envoyée à l'admin"
        await signup_flow(U6, "confirm@mail.com")
        c = w.last(U6)
        assert "confirme-le" in c.text and {"onb:ssid", "onb:login"} <= set(cbs(c)) and w.store.account(U6)["verified"]
        # limite d'essais : 3 par jour et par utilisateur
        for i in range(3):
            await signup_flow(U7, f"exists{i}@mail.com")
        await w.say(U7, "exists9@mail.com")
        assert "Trop d'essais" in w.last(U7).text or w.data[U7].get("await") != ("onb_email",)
        print("✓ erreurs de création : déjà inscrit / sans consentement / CAPTCHA (admin prévenu + capture) / confirmation / limite d'essais")

        # ============================================================ DÉJÀ INSCRIT (vérification partenaire)
        await w.start(U5)
        await w.tap(U5, "onb:have")
        await w.say(U5, "abc")
        assert w.data[U5].get("await") == ("onb_id",)
        await w.say(U5, "99999999")
        nf = w.last(U5)
        assert "Je ne trouve pas cet ID" in nf.text and "onb:have" in cbs(nf) and not w.store.account(U5)["verified"]
        await w.tap(U5, "onb:have")
        await w.say(U5, "ID: 11 111 111")
        con = w.last(U5)
        assert w.store.account(U5)["verified"] and w.store.account(U5)["po_id"] == "11111111"
        assert w.store.account(U5)["verify_source"] == "partners"
        assert cbs(con) == ["onb:ssid", "onb:login", "menu"] and labels(con)[:2] == ["🔑 TON SSID", "🔐 SE CONNECTER"]
        assert "ID vérifié" in con.text and any(m.text == "⌨️" for m in texts(w, U5))
        await w.start(U8)                                                   # un ID ne sert qu'à un seul compte Telegram
        await w.tap(U8, "onb:have")
        await w.say(U8, "11111111")
        assert "déjà lié" in w.last(U8).text and not w.store.account(U8)["verified"]
        print("✓ DÉJÀ INSCRIT : ID introuvable / ID trouvé chez le partenaire / ID unique par compte")

        # ============================================================ TON SSID (cookie ou message complet)
        msg, _ = await w.tap(U5, "onb:ssid")
        assert "ci_session" in msg.text and {"set:ss:demo", "set:ss:real"} <= set(cbs(msg))
        await w.tap(U5, "set:ss:demo", msg)
        user_msg = await w.say(U5, quote(M.session_for("moi@mail.com"), safe=""))      # simple valeur du cookie
        s = w.store.get_ssid(U5, "demo")
        assert user_msg.deleted and ssidlib.po_uid(s) == 11111111 and ssidlib.parse_ssid(s)[1] is True
        await w.tap(U5, "set:ss:real")
        await asyncio.sleep(1.6)
        await w.say(U5, M.session_for("moi@mail.com"))                                  # cookie décodé, côté réel
        assert ssidlib.parse_ssid(w.store.get_ssid(U5, "real"))[1] is False
        print("✓ TON SSID : cookie collé → SSID construit avec l'ID vérifié (démo et réel)")

        # ============================================================ SE CONNECTER (vrai navigateur)
        msg, _ = await w.tap(U5, "onb:login")
        assert "jamais enregistrés" in msg.text and w.data[U5]["await"] == ("onb_lemail",)
        await w.say(U5, "paul@mail.com")
        assert w.data[U5]["await"] == ("onb_lpw",)
        await w.say(U5, PW)                                                             # ID PO de paul ≠ ID vérifié
        assert "n'est pas celui que tu as validé" in w.last(U5).text
        assert "lemail" not in w.data[U5], "e-mail oublié après usage"
        # compte dont l'ID correspond à l'ID vérifié
        pid = M.trader_id_for("paul@mail.com")
        w.store.set_verification(U9, "ok", pid, source="admin")
        await w.start(U9)
        await w.tap(U9, "onb:login")
        await w.say(U9, "paul@mail.com")
        up_msgs = len(w.bot.sent)
        pw_msg = await w.say(U9, PW)
        assert pw_msg.deleted, "le message contenant le mot de passe est supprimé aussitôt"
        done = w.last(U9)
        assert "Connecté" in done.text and pid in done.text and "SSID démo enregistré" in done.text
        assert ssidlib.po_uid(w.store.get_ssid(U9, "demo")) == int(pid) and w.store.get_ssid(U9, "real")
        assert not datadir_has(tmp, PW), "le mot de passe de connexion n'est jamais stocké"
        await w.tap(U9, "onb:login")
        await w.say(U9, "paul@mail.com")
        await w.say(U9, "wrong")
        assert "Identifiants refusés" in w.last(U9).text and {"onb:ssid", "onb:login"} <= set(cbs(w.last(U9)))
        await w.tap(U9, "onb:login")
        await w.say(U9, "2fa@mail.com")
        await w.say(U9, PW)
        assert "double authentification" in w.last(U9).text
        print("✓ SE CONNECTER : ID vérifié requis, SSID démo+réel, mot de passe supprimé/non stocké, mauvais mot de passe, 2FA")

        # ============================================================ validation manuelle par un admin
        w.app.cfg = dataclasses.replace(w.app.cfg, verify_mode="manual")
        await w.start(U8)
        await w.tap(U8, "onb:have")
        await w.say(U8, "44444444")
        assert w.store.account(U8)["verify_status"] == "pending" and "Demande envoyée" in w.last(U8).text
        adm_msg = find(w, ADMIN, "Validation d'ID demandée")[-1]
        assert {f"adm:ver:ok:{U8}", f"adm:ver:no:{U8}"} <= set(cbs(adm_msg)) and "44444444" in adm_msg.text
        await w.start(U8)
        assert "Vérification en cours" in w.last(U8).text, "écran d'attente tant que ce n'est pas validé"
        msg, _ = await w.tap(ADMIN, "adm:pend")
        assert f"adm:ver:ok:{U8}" in cbs(msg) and "44444444" in msg.text
        await w.tap(ADMIN, f"adm:ver:ok:{U8}")
        assert w.store.account(U8)["verified"] and w.store.account(U8)["verify_source"] == "admin"
        note = find(w, U8, "Ton ID est validé")[-1]
        assert {"onb:ssid", "onb:login"} <= set(cbs(note))
        await w.start(U10)
        await w.tap(U10, "onb:have")
        await w.say(U10, "55555555")
        await w.tap(ADMIN, f"adm:ver:no:{U10}")
        assert w.store.account(U10)["verify_status"] == "rejected" and "pas pu être validé" in w.last(U10).text
        await w.tap(ADMIN, f"adm:ver:no:{U8}")                                         # révocation
        assert not w.store.account(U8)["verified"]
        msg, _ = await w.tap(U8, "menu")
        assert "Je suis LEGIT AI" in msg.text
        # partenaire indisponible en mode auto : repli sur la validation manuelle + admin prévenu
        w.app.cfg = dataclasses.replace(w.app.cfg, verify_mode="auto")
        async def down(pid, fresh=False): return W.PartnerResult("error", "site en panne")
        real_check, w.app.partners.check = w.app.partners.check, down
        await w.start(U2)
        await w.tap(U2, "onb:have")
        await w.say(U2, "66666666")
        assert w.store.account(U2)["verify_status"] == "pending"
        assert any("Vérification partenaire indisponible" in m.text for m in texts(w, ADMIN))
        w.app.partners.check = real_check
        w.app.cfg = dataclasses.replace(w.app.cfg, verify_mode="off")                  # mode « off » : accepté directement
        await w.start(U3)
        await w.tap(U3, "onb:have")
        await w.say(U3, "77777777")
        assert w.store.account(U3)["verified"] and w.store.account(U3)["verify_source"] == "off"
        w.app.cfg = dataclasses.replace(w.app.cfg, verify_mode="auto")
        print("✓ validation admin (attente / valider / refuser / révoquer) · repli si partenaire en panne · mode off")

        # ============================================================ bonus, dépôts, FAQ
        before = w.store.tokens(U5)
        msg, _ = await w.tap(U5, "bonus")
        assert w.store.tokens(U5) == before + 5 and "+5 jetons" in msg.text
        msg, _ = await w.tap(U5, "bonus")
        assert w.store.tokens(U5) == before + 5 and "déjà pris" in msg.text
        msg, _ = await w.tap(U5, "dep")
        assert len(urls(msg)) == 5 and "dep:check" in cbs(msg) and "50START" in msg.text
        t0 = w.store.tokens(U5)
        msg, _ = await w.tap(U5, "dep:check")                       # pocketpartners : dépôts 1250.50 $ pour 11111111
        gain = W.tokens_for(w.app.cfg.deposit_packs, 1250.5)
        assert w.store.tokens(U5) == t0 + gain and "Dépôt détecté" in msg.text, msg.text
        assert w.store.account(U5)["dep_credited"] == 1250.5
        msg, _ = await w.tap(U5, "dep:check")
        assert "Patiente" in msg.text
        w.app._rl.clear()
        msg, _ = await w.tap(U5, "dep:check")
        assert "Aucun nouveau dépôt" in msg.text and w.store.tokens(U5) == t0 + gain, "pas de double crédit"
        # ID absent du rapport partenaire : demande transmise à l'admin, qui crédite le pack
        t1 = w.store.tokens(U1)
        msg, _ = await w.tap(U1, "dep:check")
        assert "Demande envoyée" in msg.text
        req = find(w, ADMIN, "Dépôt à vérifier")[-1]
        assert f"adm:dep:{U1}:50" in cbs(req)
        msg, _ = await w.tap(ADMIN, f"adm:dep:{U1}:50")
        assert w.store.tokens(U1) == t1 + 600 and "+600" in " ".join(m.text for m in texts(w, U1)[-2:])
        msg, _ = await w.tap(U1, "faq")
        assert "FAQ" in msg.text and "jeton" in msg.text and "https://t.me/support" in urls(msg)
        print("✓ bonus (cooldown) · dépôt détecté auto (sans double crédit) · demande admin + crédit du pack · FAQ")

        # ============================================================ admin : fiche, recherche, diagnostic
        msg, _ = await w.tap(ADMIN, "adm")
        assert {"adm:pend", "adm:find", "adm:diag"} <= set(cbs(msg))
        await w.tap(ADMIN, "adm:find")
        await w.say(ADMIN, "11111111")
        fiche = w.last(ADMIN)
        assert "ID Pocket Option : <code>11111111</code>" in fiche.text and "vérifié (partners)" in fiche.text
        assert f"adm:ver:no:{U5}" in cbs(fiche) and "SSID : 🟠 ✅ · 🟢 ✅" in fiche.text
        await w.tap(ADMIN, "adm:find")
        await w.say(ADMIN, "zzzzzz")
        assert "Aucun résultat" in w.last(ADMIN).text
        msg, _ = await w.tap(ADMIN, "adm:diag")
        assert "Playwright : ✅" in msg.text and "Navigateur : ✅" in msg.text and "Création de compte : ✅ prête" in msg.text
        assert "Vérification d'ID : <b>auto</b>" in msg.text and "pocketpartners : ✅" in msg.text
        msg, _ = await w.tap(ADMIN, "adm:diagp")
        assert "Test pocketpartners : ✅" in msg.text
        print("✓ admin : fiche (ID, vérification, SSID) · recherche · diagnostic + test pocketpartners")

        await w.app.shutdown()
        await asyncio.sleep(0.3)
        left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert not left, left
    srv.shutdown()
    print("onboarding OK")


if __name__ == "__main__":
    asyncio.run(run())
