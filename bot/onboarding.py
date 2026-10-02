"""Parcours d'arrivée : accueil › INSCRIPTION / DÉJÀ INSCRIT › vérification de l'ID › TON SSID / SE CONNECTER.

Garde-fous : consentement explicite avant toute création de compte (âge, conditions, risques) ; mot de passe généré
affiché UNE fois puis supprimé du chat ; identifiants de connexion jamais conservés (seul le SSID l'est) ; limites
d'essais par utilisateur ; en cas d'échec de l'automatisation, repli systématique sur le lien d'inscription manuel."""
import logging

import po_web as W
from bot.ui import B, E, U, ask, drop, show

log = logging.getLogger("onboarding")

NOTICE = "⚠️ Le trading comporte un risque de perte en capital."
PW_TTL = 600          # le message contenant le mot de passe est supprimé au bout de 10 minutes


def needs(app, uid: int) -> bool:
    """Doit encore passer par l'inscription / la vérification d'ID."""
    return bool(app.cfg.require_verified and not app.is_admin(uid) and not app.store.account(uid)["verified"])


def _reg_button(app):
    return [U("S'INSCRIRE", app.cfg.po_register_url)] if app.cfg.po_register_url else []


# ======================================================================================== écrans
async def screen_welcome(app, update):
    uid = update.effective_user.id
    acc = app.store.account(uid)
    if acc["verify_status"] == "pending":
        await show(update, "⏳ <b>Vérification en cours</b>\n\nTon ID Pocket Option "
                           f"<code>{E(acc['po_id'])}</code> est en attente de validation. Tu seras prévenu ici dès que c'est fait.",
                   [[B("✏️ Changer d'ID", "onb:have")]] + ([[U("🆘 Support", app.cfg.support_url)]] if app.cfg.support_url else []))
        return
    first = app.cfg.welcome_text or f"👋 Salut ! Je suis {app.cfg.bot_name}. Bienvenue dans le monde des millionnaires 🤑💰."
    rows = [[B("INSCRIPTION", "onb:reg"), B("DÉJÀ INSCRIT", "onb:have")]]
    if app.cfg.support_url:
        rows.append([U("🆘 Support", app.cfg.support_url)])
    await show(update, f"{E(first)}\n\n<i>{NOTICE}</i>", rows)


async def screen_register(app, update):
    rows = []
    if app.cfg.auto_signup and app.web.available() and app.cfg.po_register_url:
        rows.append([B("TE CRÉER UN COMPTE", "onb:new")])
    rows += [_reg_button(app)] if app.cfg.po_register_url else []
    rows += [[B("DÉJÀ INSCRIT", "onb:have")], [B("⬅️ Retour", "onb")]]
    await show(update, "📝 <b>Inscription</b>\n\n• <b>TE CRÉER UN COMPTE</b> : je crée ton compte Pocket Option avec ton e-mail.\n"
                       "• <b>S'INSCRIRE</b> : tu t'inscris toi-même avec mon lien, puis tu touches « DÉJÀ INSCRIT ».", rows)


async def screen_consent(app, update):
    await show(update, "📜 <b>Avant de continuer</b>\n\nJe vais créer un compte Pocket Option <b>à ton nom</b> avec ton e-mail. "
                       "En continuant, tu confirmes :\n• avoir <b>18 ans ou plus</b> ;\n"
                       "• avoir lu et accepté les <b>conditions d'utilisation</b> et l'avertissement sur les risques de Pocket Option ;\n"
                       "• que le trading comporte un <b>risque de perte en capital</b> ;\n• que cet e-mail est <b>le tien</b>.",
               [[B("✅ J'accepte et je continue", "onb:consent")], [B("❌ Annuler", "onb:reg")]])


async def ask_email(app, update, ctx):
    await ask(update, ctx, "Je vais créer un compte de trading et lancer une série de trades démo.\n\n"
                           "<b>Entrez votre e-mail pour vous inscrire :</b>", [[B("⬅️ Annuler", "onb:reg")]], ("onb_email",))


async def screen_connect(app, update, note: str = ""):
    await show(update, (f"{note}\n\n" if note else "") + "✅ <b>ID vérifié !</b>\n\nConnecte maintenant ton compte Pocket Option "
                       "pour que je puisse trader pour toi :",
               [[B("🔑 TON SSID", "onb:ssid"), B("🔐 SE CONNECTER", "onb:login")], [B("⏭ Plus tard", "menu")]])


def connect_rows():
    return [[B("🔑 TON SSID", "onb:ssid"), B("🔐 SE CONNECTER", "onb:login")], [B("⏭ Plus tard", "menu")]]


async def screen_ssid_help(app, update):
    await show(update,
               "🔑 <b>Récupérer ton SSID</b>\n\nLe SSID est la clé de session de ton compte. Deux méthodes :\n\n"
               "<b>1) Simple — le cookie</b>\n• Ouvre Pocket Option dans Chrome et connecte-toi.\n"
               "• F12 › onglet <i>Application</i> › <i>Cookies</i> › pocketoption.com.\n"
               "• Copie la <b>valeur</b> du cookie <code>ci_session</code> et envoie-la ici.\n\n"
               "<b>2) Complète — le message WebSocket</b>\n• F12 › <i>Network</i> › <i>WS</i> › ouvre la connexion › <i>Messages</i>.\n"
               "• Copie le message qui commence par <code>42[\"auth\"</code>.\n\n"
               "🔐 Ton message est supprimé automatiquement ; je teste ensuite la connexion.",
               [[B("🟠 Envoyer mon SSID DÉMO", "set:ss:demo")], [B("🟢 Envoyer mon SSID RÉEL", "set:ss:real")],
                [B("⬅️ Retour", "onb:ok")]])


async def screen_login_help(app, update, ctx):
    await ask(update, ctx,
              "🔐 <b>Se connecter</b>\n\nEnvoie ton <b>e-mail</b> Pocket Option, puis ton <b>mot de passe</b>.\n"
              "• Ils servent <b>une seule fois</b> à récupérer ta session ; ils ne sont <b>jamais enregistrés</b>.\n"
              "• Tes messages sont supprimés aussitôt.\n• Plus sûr : utilise plutôt « TON SSID ».\n\n<b>Ton e-mail :</b>",
              [[B("⬅️ Retour", "onb:ok")]], ("onb_lemail",))


# ======================================================================================== étapes
def _limit(app, uid: int, kind: str, per_day: int):
    """Message d'erreur si trop d'essais (par utilisateur / par heure au global), sinon None."""
    if not app.rate_ok(f"{kind}:{uid}", per_day, 86400):
        return "Trop d'essais aujourd'hui. Réessaie demain, ou utilise le lien d'inscription / TON SSID."
    if kind == "signup" and not app.rate_ok("signup:*", app.cfg.signup_per_hour_global, 3600):
        return "Beaucoup de demandes en ce moment. Réessaie dans une heure, ou inscris-toi avec le lien."
    return None


async def _pw_message(app, rt, text: str, rows):
    """Envoie les identifiants puis programme leur suppression."""
    msg = await app.send(rt, text, rows)
    if msg is not None:
        app.later(rt, PW_TTL, drop, msg)
    return msg


async def handle_email(app, update, ctx, rt, text: str, deleted: bool):
    uid = rt.uid
    email = text.strip().lower()
    if not W.valid_email(email):
        raise ValueError("Adresse e-mail invalide — exemple : prenom.nom@gmail.com")
    acc = app.store.account(uid)
    if acc["verified"] and acc["po_id"]:
        ctx.user_data.pop("await", None)
        await drop(ctx.user_data.pop("prompt", None))
        await show(update, "Tu as déjà un compte lié 👍", [[B("🏠 Menu", "menu")]])
        return
    limit = _limit(app, uid, "signup", app.cfg.signup_per_day)
    ctx.user_data.pop("await", None)
    await drop(ctx.user_data.pop("prompt", None))
    if limit:
        await show(update, f"⏳ {limit}", [_reg_button(app), [B("DÉJÀ INSCRIT", "onb:have")]] if app.cfg.po_register_url
                   else [[B("DÉJÀ INSCRIT", "onb:have")]])
        return
    app.rate_hit(f"signup:{uid}")
    app.rate_hit("signup:*")
    prog = await show(update, "⏳ <b>Création de ton compte…</b>\nCela peut prendre jusqu'à une minute, ne ferme pas la conversation.", [])
    pwd = W.strong_password()
    res = await app.web.signup(email, pwd, accept_terms=bool(acc["consent"]))
    await drop(prog)
    log.info("inscription %s -> %s", W.mask_email(email), res.status)
    fallback = [[b for b in _reg_button(app)], [B("DÉJÀ INSCRIT", "onb:have")], [B("🔁 Réessayer", "onb:new")]]
    fallback = [r for r in fallback if r]
    creds = (f"📧 Identifiant : <code>{E(email)}</code>\n🔑 Mot de passe : <code>{E(pwd)}</code>\n"
             + (f"🆔 ID Pocket Option : <code>{E(res.trader_id)}</code>\n" if res.trader_id else "")
             + "\n📌 <b>Note-les maintenant</b> : ce message sera supprimé dans 10 minutes.")

    if res.status == "ok":
        po_id = res.trader_id
        if po_id and app.store.po_owner(po_id) not in (None, uid):
            po_id = ""                                                  # déjà lié à un autre compte : on ne l'écrase pas
        app.store.set_verification(uid, "ok", po_id or None, source="bot")
        note = ""
        if po_id and res.session:
            ok, info = await app.store_ssid(rt, "demo", res.ssid(True))
            await app.store_ssid(rt, "real", res.ssid(False), check=False)
            note = ("\n🟠 SSID démo enregistré · " + ("🟢 connecté" if ok else "⚠️ à vérifier") + "\n"
                    "🟢 SSID réel enregistré (utilisable si le mode réel est activé)")
        else:
            note = "\nℹ️ Je n'ai pas pu lire ton ID/ta session : enregistre ton SSID dans ⚙️ Paramètres › Mes comptes."
        await app.install_keyboard(rt)
        await _pw_message(app, rt, f"✅ <b>Compte créé !</b>\n\n{creds}{note}", [[B("🏠 Menu", "menu")]])
        return
    if res.status in ("confirm_email", "created_nosession") or (res.status == "timeout" and res.submitted):
        verified = res.status in ("confirm_email", "created_nosession")
        if verified:
            app.store.set_verification(uid, "ok", None, source="bot")
        why = {"confirm_email": "Pocket Option t'a envoyé un e-mail : <b>confirme-le</b>, puis touche « SE CONNECTER ».",
               "created_nosession": "Ton compte semble créé. Touche « SE CONNECTER » pour me lier ton compte.",
               "timeout": "Je n'ai pas reçu la réponse du site : l'inscription a <b>peut-être</b> abouti. Vérifie ta boîte mail, "
                          "puis touche « SE CONNECTER » (ou « DÉJÀ INSCRIT » avec ton ID)."}[res.status]
        await _pw_message(app, rt, f"✅ <b>Inscription envoyée</b>\n\n{creds}\n\n{why}",
                          connect_rows() if verified else [[B("DÉJÀ INSCRIT", "onb:have")]])
        if verified:
            await app.install_keyboard(rt)
        return
    if res.status == "exists":
        await show(update, "ℹ️ Cet e-mail a <b>déjà</b> un compte Pocket Option.\nTouche « DÉJÀ INSCRIT » et envoie ton ID "
                           "(attention : pour être validé, le compte doit avoir été créé avec mon lien).",
                   [[B("DÉJÀ INSCRIT", "onb:have")], [B("⬅️ Retour", "onb:reg")]])
        return
    if res.status == "invalid_email":
        await ask_email(app, update, ctx)
        ctx.user_data["prompt_text"] = "⚠️ Pocket Option refuse cet e-mail.\n\n" + ctx.user_data["prompt_text"]
        return
    # captcha / form_error / timeout avant envoi / erreur / indisponible : repli manuel, l'admin est prévenu
    reasons = {"captcha": "Pocket Option demande une vérification anti-robot (je ne la contourne pas).",
               "unavailable": "La création automatique n'est pas disponible pour le moment.",
               "form_error": "Le site a refusé le formulaire."}
    await show(update, f"⚠️ <b>Création automatique impossible</b>\n\n{reasons.get(res.status, 'Le site ne répond pas comme prévu.')}\n\n"
                       "Inscris-toi toi-même avec le lien, puis touche « DÉJÀ INSCRIT » et envoie ton ID.", fallback)
    if res.status != "invalid_email":
        await app.notify_admins(f"⚠️ Inscription automatique en échec : <b>{E(res.status)}</b>\n{E(res.detail[:200])}", photo=res.diag)


async def ask_id(app, update, ctx):
    await ask(update, ctx, "<b>Envoie ton ID Pocket Option :</b>\n(visible dans ton profil, 5 à 12 chiffres)",
              [[B("⬅️ Retour", "onb")]], ("onb_id",))


async def handle_id(app, update, ctx, rt, text: str):
    uid = rt.uid
    po_id = W.normalize_trader_id(text)
    if not po_id:
        raise ValueError("ID invalide — c'est un nombre de 5 à 12 chiffres, visible dans ton profil Pocket Option")
    ctx.user_data.pop("await", None)
    await drop(ctx.user_data.pop("prompt", None))
    owner = app.store.po_owner(po_id)
    if owner not in (None, uid):
        await show(update, "⛔ Cet ID est déjà lié à un autre compte Telegram.",
                   [[U("🆘 Support", app.cfg.support_url)]] if app.cfg.support_url else [[B("⬅️ Retour", "onb")]])
        return
    await verify_id(app, update, rt, po_id)


async def verify_id(app, update, rt, po_id: str):
    uid, mode = rt.uid, app.cfg.verify_mode
    if mode == "auto":
        prog = await show(update, "⏳ <b>Vérification de ton ID…</b>", [])
        res = await app.partners.check(po_id)
        await drop(prog)
        if res.status == "found":
            return await _accept(app, update, rt, po_id, "partners")
        if res.status == "not_found":
            rows = ([_reg_button(app)] if app.cfg.po_register_url else []) + [[B("🔁 Réessayer", "onb:have")]]
            if app.cfg.support_url:
                rows.append([U("🆘 Support", app.cfg.support_url)])
            await show(update, "❌ <b>Je ne trouve pas cet ID parmi mes filleuls.</b>\n\nIl faut t'être inscrit avec <b>mon lien</b>. "
                               "Si tu viens de t'inscrire, la synchronisation peut prendre quelques minutes : réessaie.", rows)
            return
        await app.notify_admins(f"⚠️ Vérification partenaire indisponible ({E(res.status)} : {E(res.detail[:150])}). "
                                "Validation manuelle demandée.", photo=res.diag)
    elif mode == "off":
        return await _accept(app, update, rt, po_id, "off")
    await _pending(app, update, rt, po_id)


async def _accept(app, update, rt, po_id: str, source: str):
    app.store.set_verification(rt.uid, "ok", po_id, source=source)
    await app.install_keyboard(rt)
    await screen_connect(app, update)


async def _pending(app, update, rt, po_id: str):
    uid = rt.uid
    app.store.set_verification(uid, "pending", po_id, source="manual")
    acc = app.store.account(uid)
    who = f"@{acc['username']}" if acc["username"] else (acc["name"] or str(uid))
    await app.notify_admins(f"⏳ <b>Validation d'ID demandée</b>\n{E(who)} (Telegram <code>{uid}</code>)\n"
                            f"ID Pocket Option : <code>{E(po_id)}</code>",
                            rows=[[B("✅ Valider", f"adm:ver:ok:{uid}"), B("❌ Refuser", f"adm:ver:no:{uid}")]])
    await show(update, "⏳ <b>Demande envoyée</b>\n\nTon ID est en cours de validation par l'équipe. Tu seras prévenu ici.",
               [[U("🆘 Support", app.cfg.support_url)]] if app.cfg.support_url else [])


async def handle_login_email(app, update, ctx, rt, text: str):
    email = text.strip().lower()
    if not W.valid_email(email):
        raise ValueError("Adresse e-mail invalide")
    ctx.user_data["lemail"] = email
    ctx.user_data.pop("await", None)
    await drop(ctx.user_data.pop("prompt", None))
    await ask(update, ctx, "🔐 Envoie maintenant ton <b>mot de passe</b> Pocket Option.\n(Il est supprimé aussitôt et jamais enregistré.)",
              [[B("⬅️ Annuler", "onb:ok")]], ("onb_lpw",))


async def handle_login_password(app, update, ctx, rt, password: str, deleted: bool):
    uid = rt.uid
    email = ctx.user_data.pop("lemail", "")
    ctx.user_data.pop("await", None)
    await drop(ctx.user_data.pop("prompt", None))
    password = password.strip()
    if not email or not password:
        await show(update, "⚠️ Session expirée, recommence.", [[B("🔐 SE CONNECTER", "onb:login")]])
        return
    limit = _limit(app, uid, "login", 5)
    if limit:
        await show(update, f"⏳ {limit}", [[B("🔑 TON SSID", "onb:ssid")]])
        return
    app.rate_hit(f"login:{uid}")
    warn = "" if deleted else "\n⚠️ Supprime toi-même ton message contenant le mot de passe."
    prog = await show(update, "⏳ <b>Connexion à Pocket Option…</b>", [])
    res = await app.web.login(email, password)
    await drop(prog)
    del password
    log.info("connexion %s -> %s", W.mask_email(email), res.status)
    alt = [[B("🔑 TON SSID", "onb:ssid")], [B("🔁 Réessayer", "onb:login")]]
    if res.status != "ok":
        msg = {"bad_credentials": "Identifiants refusés par Pocket Option.",
               "twofa": "Ton compte demande un code de double authentification : utilise « TON SSID ».",
               "captcha": "Pocket Option demande une vérification anti-robot : utilise « TON SSID ».",
               "confirm_email": "Confirme d'abord ton e-mail (message de Pocket Option), puis réessaie."}.get(
            res.status, "Connexion automatique impossible pour le moment : utilise « TON SSID ».")
        await show(update, f"⚠️ {msg}{warn}", alt)
        if res.status in ("error", "unavailable", "timeout"):
            await app.notify_admins(f"⚠️ Connexion automatique en échec : <b>{E(res.status)}</b>\n{E(res.detail[:200])}", photo=res.diag)
        return
    acc = app.store.account(uid)
    tid = res.trader_id
    if not tid:
        await show(update, "⚠️ Connecté, mais je n'ai pas pu lire ton ID : utilise « TON SSID ».", alt)
        return
    if acc["po_id"] and acc["po_id"] != tid:                          # ce n'est pas le compte vérifié
        await show(update, f"⛔ Ce compte (ID {E(tid)}) n'est pas celui que tu as validé (ID {E(acc['po_id'])}).{warn}", alt)
        return
    if app.store.po_owner(tid) not in (None, uid):
        await show(update, "⛔ Cet ID est déjà lié à un autre compte Telegram.", alt)
        return
    if not acc["po_id"]:
        app.store.set_verification(uid, "ok" if acc["verified"] else acc["verify_status"], tid)
    ok, info = await app.store_ssid(rt, "demo", res.ssid(True))
    await app.store_ssid(rt, "real", res.ssid(False), check=False)
    await app.install_keyboard(rt)
    await show(update, f"✅ <b>Connecté !</b> Compte <code>{E(tid)}</code>\n🟠 SSID démo enregistré · "
                       + ("🟢 connexion testée" if ok else "⚠️ à vérifier") + "\n🟢 SSID réel enregistré" + warn,
               [[B("🏠 Menu", "menu")]])
