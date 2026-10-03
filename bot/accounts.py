"""Écrans comptes : jetons, canal principal, amis, support (utilisateurs) et administration (admins)."""
import asyncio
import time
from datetime import datetime
from urllib.parse import quote

from telegram import InlineKeyboardMarkup as M
from telegram.error import TelegramError

import runtime_settings as RS
import texts
import views
import visuals
from bot.ui import B, E, PANELS, U, drop, show
from strategies import REGISTRY

PAGE = 8


# ==================================================================== utilisateurs
def token_label(app, uid: int) -> str:
    return "∞" if app.is_admin(uid) else str(app.store.tokens(uid))


def support_button(app):
    return U("🆘 Support", app.cfg.support_url) if app.cfg.support_url else B("🆘 Support", "sup")


def channel_label(app, uid: int) -> str:
    return "📢 Canal principal" + (" ✅" if app.store.account(uid)["channel_ok"] else "")


def invite_link(app, uid: int) -> str:
    return f"https://t.me/{app.bot_username}?start=ref_{uid}" if app.bot_username else ""


def pack_rows(app):
    """Un bouton par pack : ouvre la page de dépôt avec le montant (DEPOSIT_URL du .env)."""
    c = app.cfg
    return [[U(f"💰 ${usd} ➔ {tok} 💎", c.deposit_url.format(amount=usd))] for usd, tok in c.deposit_packs]


def promo_line(app) -> str:
    c = app.cfg
    return (f"🎁 Code promo <code>{E(c.promo_code)}</code> ➔ {E(c.promo_text)} (touchez le code pour copier).\n\n"
            if c.promo_code else "")


async def screen_tokens(app, update, uid: int):
    c = app.cfg
    await show(update,
               texts.get("tokens", cost_real=c.cost_auto_real, cost_demo=c.cost_auto_demo, tokens=token_label(app, uid))
               + "\n\n" + promo_line(app).rstrip("\n"),
               pack_rows(app) + [[B("✅ J'ai déposé — vérifier", "dep:check")],
                                 [B(f"🎁 Bonus ({c.bonus_tokens} 💎)", "bonus"), B("👥 Amis", "fr")],
                                 [B("⬅️ Menu", "menu")]], banner="tokens")


async def screen_deposit(app, update, uid: int):
    await show(update,
               texts.get("deposit") + "\n\n" + promo_line(app) + texts.get("deposit_after"),
               pack_rows(app) + [[B("✅ J'ai déposé — vérifier", "dep:check")], [B("⬅️ Menu", "menu")]], banner="deposit")


async def deposit_check(app, update, uid: int):
    """Détecte les nouveaux dépôts via pocketpartners et crédite les jetons ; sinon demande une validation admin."""
    import po_web as W
    acc = app.store.account(uid)
    back = [[B("💎 Jetons", "tok"), B("⬅️ Menu", "menu")]]
    if not acc["po_id"]:
        await show(update, "ℹ️ Je ne connais pas encore ton ID Pocket Option : passe par « DÉJÀ INSCRIT ».", back)
        return
    if not app.rate_ok(f"dep:{uid}", 1, 120):
        await show(update, "⏳ Patiente 2 minutes avant une nouvelle vérification.", back)
        return
    app.rate_hit(f"dep:{uid}")
    if app.cfg.verify_mode == "auto" and app.partners.configured():
        await show(update, "⏳ <b>Vérification de ton dépôt…</b>", [])
        res = await app.partners.check(acc["po_id"], fresh=True)
        if res.status == "found" and res.deposits is not None:
            delta = round(res.deposits - acc["dep_credited"], 2)
            tokens = W.tokens_for(app.cfg.deposit_packs, delta) if delta > 0 else 0
            if tokens > 0:
                app.store.add_tokens(uid, tokens)
                app.store.add_dep_credited(uid, delta)
                await show(update, f"✅ Dépôt détecté : <b>${delta:g}</b> ➔ <b>+{tokens} 💎</b>\nSolde : <b>{app.store.tokens(uid)}</b>", back)
            else:
                await show(update, f"Aucun nouveau dépôt détecté (total vu : ${res.deposits:g}). Réessaie dans quelques minutes.", back)
            return
    who = f"@{acc['username']}" if acc["username"] else (acc["name"] or str(uid))
    amounts = [[B(f"+${usd} ➔ {tok} 💎", f"adm:dep:{uid}:{usd}")] for usd, tok in app.cfg.deposit_packs]
    await app.notify_admins(f"💰 <b>Dépôt à vérifier</b>\n{E(who)} (<code>{uid}</code>) · ID PO <code>{E(acc['po_id'])}</code>\n"
                            "Créditer le pack correspondant :", rows=amounts)
    await show(update, "📨 Demande envoyée à l'équipe : tes jetons seront crédités après vérification.", back)


async def screen_bonus(app, update, uid: int):
    c = app.cfg
    ok, left = app.store.claim_bonus(uid, c.bonus_tokens, c.bonus_hours)
    if ok:
        text = (f"🎁 <b>+{c.bonus_tokens} jetons</b> offerts !\n💎 Solde : <b>{app.store.tokens(uid)}</b>\n"
                + (f"Prochain bonus dans {c.bonus_hours} h." if c.bonus_hours > 0 else "Bonus unique."))
    elif c.bonus_hours <= 0:
        text = "🎁 Tu as déjà pris ton bonus unique."
    else:
        text = f"⏳ Bonus déjà pris. Prochain dans <b>{left // 3600} h {left % 3600 // 60:02d} min</b>."
    await show(update, text, [[B("💎 Jetons", "tok")], [B("⬅️ Menu", "menu")]], banner="bonus")


DEFAULT_FAQ = """❓ FAQ

🤖 Que fait le bot ?
Il trade des options binaires pour vous sur PocketOption — automatiquement ou manuellement, sur comptes démo et réel.

💎 Que sont les jetons ?
Carburant pour l'auto-trading : 1 session complète = 1 jetons en réel, 1 en démo. Le trading manuel est gratuit. Vous gagnez des jetons via les dépôts, le bonus quotidien et les amis invités.

💰 Comment passer en argent réel ?
Rechargez votre compte (bonus +60% au dépôt) et passez le mode sur « Réel » dans les paramètres.

🎲 Stratégies et indicateurs ?
Une stratégie gère la taille de la mise, un indicateur donne le sens du trade. Elles diffèrent par la forme du risque — aucune ne garantit de profit.

📈 Le profit est-il garanti ?
Non. Les taux de réussite affichés sont des stats réelles de trades réels, pas des promesses. Les options binaires comportent un risque de perte ; les stratégies de mise n'ont aucun avantage sur le marché.

💸 Dépôt et retrait ?
Se font sur la page du courtier. Ne déposez que ce que vous pouvez perdre.

🆘 Support
Pour toute question — (support et propriétaire du bot)."""


def faq_text(app) -> str:
    from pathlib import Path
    path = Path(app.cfg.faq_file) if app.cfg.faq_file else Path(__file__).resolve().parents[1] / "faq.txt"
    txt = texts.override("faq") or ""                          # FAQ modifiée depuis Telegram (Admin › Textes) : prioritaire
    if not txt:
        try:
            txt = path.read_text(encoding="utf-8").strip()
        except OSError:
            txt = DEFAULT_FAQ
    return E(txt[:3800] or DEFAULT_FAQ)


async def screen_faq(app, update):
    await show(update, faq_text(app), [[support_button(app)], [B("⬅️ Menu", "menu")]])


async def screen_no_tokens(app, update, uid: int):
    await show(update, texts.get("no_tokens"), [[B("💎 Jetons", "tok")], [B("⬅️ Menu", "menu")]], banner="no_tokens")


async def screen_channel(app, update, uid: int):
    c = app.cfg
    done = app.store.account(uid)["channel_ok"]
    if not c.channel_id:
        await show(update, "📣 Le canal principal n'est pas configuré.", [[B("⬅️ Menu", "menu")]])
        return
    rows = []
    if c.channel_url:
        rows.append([U("📣 Rejoindre le canal", c.channel_url)])
    if not done:
        rows.append([B(f"✅ J'ai rejoint — vérifier (+{c.channel_bonus} 💎)", "chan:ok")])
    rows.append([B("⬅️ Menu", "menu")])
    await show(update, "📣 <b>Canal principal</b>\n\n"
                       + ("✅ Déjà vérifié — merci !" if done else
                          f"Rejoignez le canal puis touchez « Vérifier » : <b>+{c.channel_bonus} jetons</b> (une seule fois)."),
               rows, banner="community")


async def channel_verify(app, update, uid: int):
    c = app.cfg
    try:
        m = await app.bot.get_chat_member(c.channel_id, uid)
        member = getattr(m, "status", "") in ("member", "administrator", "creator") or bool(getattr(m, "is_member", False))
    except TelegramError as e:
        await show(update, f"⚠️ Vérification impossible pour le moment ({E(str(e))}).", [[B("⬅️ Retour", "chan")]])
        return
    if not member:
        await show(update, "❌ Vous n'êtes pas encore abonné au canal.\nRejoignez-le puis réessayez.",
                   ([[U("📣 Rejoindre le canal", c.channel_url)]] if c.channel_url else [])
                   + [[B("🔄 Réessayer", "chan:ok")], [B("⬅️ Menu", "menu")]])
        return
    if app.store.claim_channel(uid, c.channel_bonus):
        await show(update, f"🎉 Canal vérifié : <b>+{c.channel_bonus} jetons</b> !\n💎 Solde : <b>{token_label(app, uid)}</b>",
                   [[B("💎 Jetons", "tok"), B("⬅️ Menu", "menu")]])
    else:
        await show(update, "✅ Déjà vérifié.", [[B("⬅️ Menu", "menu")]])


async def screen_friends(app, update, uid: int):
    acc = app.store.account(uid)
    link = invite_link(app, uid)
    share = (f"https://t.me/share/url?url={quote(link)}&text={quote('Rejoins-moi sur ce bot de trading !')}") if link else ""
    await show(update,
               texts.get("friends", ref_bonus=app.cfg.ref_bonus, invited=len(acc["invited"]), earned=acc["ref_earned"],
                         link_block=(f"Votre lien :\n<code>{E(link)}</code>\n\n" if link else "Lien indisponible pour l'instant.\n\n")),
               ([[U("📤 Partager", share)]] if share else []) + [[B("⬅️ Menu", "menu")]], banner="friends")


async def screen_support(app, update):
    await show(update, "🆘 <b>Support</b>\n\nLe support n'est pas encore configuré.", [[B("⬅️ Menu", "menu")]])


# ==================================================================== administration
def user_line(uid: int, a: dict) -> str:
    who = f"@{a['username']}" if a["username"] else (a["name"] or str(uid))
    flag = "🚫" if a["blocked"] else "✅"
    return f"{flag} {who} · 💎 {a['tokens']}"


async def screen_admin(app, update):
    accs = app.store.accounts()
    n_ok = sum(1 for u, a in accs.items() if app.is_allowed(u))
    pend = len(app.store.pending_verifications())
    n_ver = sum(1 for a in accs.values() if a["verified"])
    await show(update,
               "🛠 <b>Administration</b>\n\n"
               f"Comptes : <b>{len(accs)}</b> · actifs : <b>{n_ok}</b> · ID vérifiés : <b>{n_ver}</b>\n"
               f"⏳ En attente de validation : <b>{pend}</b>\n"
               f"💎 Jetons en circulation : <b>{sum(a['tokens'] for a in accs.values())}</b>",
               [[B("👥 Utilisateurs", "adm:u:0"), B("➕ Ajouter / créditer", "adm:add")],
                [B(f"⏳ Vérifications ({pend})", "adm:pend"), B("🔎 Rechercher", "adm:find")],
                [B("📊 Stats globales", "adm:stats"), B("📣 Diffusion", "adm:bc")],
                [B("⚙️ Réglages", "adm:cfg"), B("✏️ Textes", "adm:txt")],
                [B("🖼 Images des écrans", "adm:img"), B("📊 Partager le total", "adm:sh")],
                [B("🩺 Diagnostic", "adm:diag")],
                [B("⬅️ Menu", "menu")]])


async def screen_pending(app, update):
    rows, lines = [], []
    for uid in app.store.pending_verifications()[:10]:
        a = app.store.account(uid)
        who = f"@{a['username']}" if a["username"] else (a["name"] or str(uid))
        lines.append(f"• {E(who)} · ID <code>{E(a['po_id'])}</code>")
        rows.append([B(f"✅ {who[:18]}", f"adm:ver:ok:{uid}"), B("❌", f"adm:ver:no:{uid}"), B("👤", f"adm:usr:{uid}")])
    rows.append([B("⬅️ Admin", "adm")])
    await show(update, "⏳ <b>Vérifications en attente</b>\n\n" + ("\n".join(lines) if lines else "Aucune demande en attente."), rows)


async def verification_action(app, update, target: int, ok: bool):
    """Valide / refuse / révoque l'ID d'un utilisateur et le prévient."""
    from bot import onboarding as ONB
    if ok:
        app.store.set_verification(target, "ok", source="admin")
        await app.install_keyboard(target)
        await notify_user(app, target, "✅ <b>Ton ID est validé !</b>\n\nConnecte maintenant ton compte Pocket Option "
                                       "pour que je puisse trader pour toi :", ONB.connect_rows())
    else:
        app.store.set_verification(target, "rejected", source="admin")
        rows = ([[U("S'INSCRIRE", app.cfg.po_register_url)]] if app.cfg.po_register_url else []) + [[B("✏️ Changer d'ID", "onb:have")]]
        await notify_user(app, target, "❌ Ton ID n'a pas pu être validé. Il doit provenir d'une inscription avec mon lien.", rows)
    await screen_user(app, update, target)


async def credit_deposit(app, update, target: int, usd: int):
    tokens = dict(app.cfg.deposit_packs).get(usd, 0)
    if not tokens:
        await show(update, "Pack inconnu.", [[B("⬅️ Admin", "adm")]])
        return
    bal = app.store.add_tokens(target, tokens)
    app.store.add_dep_credited(target, usd)
    await notify_user(app, target, f"✅ Dépôt de <b>${usd}</b> confirmé : <b>+{tokens} 💎</b>\nSolde : <b>{bal}</b>")
    await show(update, f"✅ +{tokens} jetons crédités (${usd}) à <code>{target}</code>.", [[B("👤 Fiche", f"adm:usr:{target}"), B("⬅️ Admin", "adm")]])


async def screen_diag(app, update, live_partner: bool = False):
    import po_web as W
    c = app.cfg
    ok_b, det = await app.web.diagnose()
    pw = "✅ installé" if app.web.available() else "❌ non installé (pip install playwright)"
    lines = [f"🩺 <b>Diagnostic</b>\n",
             f"• Playwright : {pw}", f"• Navigateur : {'✅' if ok_b else '❌'} {E(det)}",
             f"• Création de compte : " + ("✅ prête" if (c.auto_signup and c.po_register_url and ok_b) else
                                          "⚠️ " + ("AUTO_SIGNUP=0" if not c.auto_signup else "PO_REGISTER_URL manquant" if not c.po_register_url else "navigateur indisponible")),
             f"• Garde d'accès (REQUIRE_VERIFIED) : {'activée' if c.require_verified else 'désactivée'}",
             f"• Vérification d'ID : <b>{c.verify_mode}</b> · pocketpartners : {'✅ identifiants présents' if app.partners.configured() else '❌ identifiants absents'}",
             f"• Canal : {E(c.channel_id) if c.channel_id else '—'} · Support : {'✅' if c.support_url else '—'}",
             f"• Dépôt : {'✅' if '{amount}' in c.deposit_url else '⚠️ {amount} absent de DEPOSIT_URL'} · {len(c.deposit_packs)} packs"
             + (f" · promo {E(c.promo_code)}" if c.promo_code else ""),
             f"• Signaux sans SSID (flux partagé) : {'✅' if app.feed_ssid() or c.broker == 'paper' else '❌ PO_SSID_DEMO absent'}",
             f"• Mode réel : {'🔓 autorisé' if c.allow_real else '🔒 verrouillé'}",
             f"• Sélecteurs : {E(c.selectors_file) if c.selectors_file else 'par défaut'}"]
    if live_partner:
        r = await app.partners.check("0", fresh=True)
        lines.append("• Test pocketpartners : " + {"not_found": "✅ connexion et tableau OK", "found": "✅ OK"}.get(
            r.status, f"❌ {E(r.status)} — {E(r.detail[:120])}"))
    await show(update, "\n".join(lines), [[B("🧪 Tester pocketpartners", "adm:diagp")], [B("🔄 Relancer", "adm:diag"), B("⬅️ Admin", "adm")]])


async def screen_matches(app, update, ids):
    if len(ids) == 1:
        await screen_user(app, update, ids[0])
        return
    rows = [[B(user_line(u, app.store.account(u)), f"adm:usr:{u}")] for u in ids[:8]]
    rows.append([B("⬅️ Admin", "adm")])
    await show(update, f"🔎 {len(ids)} résultats" if ids else "🔎 Aucun résultat.", rows)


async def screen_users(app, update, page: int):
    accs = sorted(((u, a) for u, a in app.store.accounts().items() if u not in app.cfg.admin_ids),
                  key=lambda x: -x[1]["joined"])
    pages = max(1, (len(accs) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows = [[B(user_line(u, a), f"adm:usr:{u}")] for u, a in accs[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0: nav.append(B("◀️", f"adm:u:{page - 1}"))
    if page < pages - 1: nav.append(B("▶️", f"adm:u:{page + 1}"))
    if nav: rows.append(nav)
    rows.append([B("➕ Ajouter", "adm:add"), B("⬅️ Admin", "adm")])
    await show(update, f"👥 <b>Utilisateurs</b> ({len(accs)}) · page {page + 1}/{pages}"
                       + ("" if accs else "\n\nAucun utilisateur pour l'instant."), rows)


async def screen_user(app, update, target: int):
    a = app.store.account(target)
    trades = app.store.trades(target)
    wins = sum(1 for t in trades if t["outcome"] == "win")
    loss = sum(1 for t in trades if t["outcome"] == "loss")
    pnl = sum(t["profit"] for t in trades)
    access = "🚫 bloqué" if a["blocked"] else "✅ actif"
    last = f"\nInscrit le {time.strftime('%d/%m/%Y', time.localtime(a['joined']))}" if a["joined"] else ""
    await show(update,
               f"👤 <b>{E(a['name'] or str(target))}</b>" + (f" · @{E(a['username'])}" if a["username"] else "")
               + f"\nID : <code>{target}</code>{last}\n\n"
               f"Accès : {access}\n💎 Jetons : <b>{a['tokens']}</b>\n"
               f"🆔 ID Pocket Option : <code>{E(a['po_id']) or '—'}</code> · "
               + {"ok": f"✅ vérifié ({E(a['verify_source'] or '?')})", "pending": "⏳ en attente",
                  "rejected": "❌ refusé"}.get(a["verify_status"], "— non vérifié")
               + f"\n🔑 SSID : 🟠 {'✅' if app.store.get_ssid(target, 'demo') else '❌'} · "
               f"🟢 {'✅' if app.store.get_ssid(target, 'real') else '❌'}\n"
               f"👥 Invités : {len(a['invited'])} (+{a['ref_earned']} 💎)\n"
               f"📈 Trades : {len(trades)} · ✅ {wins} · ❌ {loss} · P&amp;L {views.money(pnl, True)}",
               [[B("➕ 10", f"adm:tk:{target}:10"), B("➕ 50", f"adm:tk:{target}:50"), B("➕ 100", f"adm:tk:{target}:100")],
                [B("✏️ Ajouter / retirer…", f"adm:tke:{target}")],
                [B("🔓 Révoquer la vérification", f"adm:ver:no:{target}") if a["verified"] else B("✅ Valider l'ID", f"adm:ver:ok:{target}")],
                [B("✅ Débloquer" if a["blocked"] else "🚫 Bloquer l'utilisateur", f"adm:acc:{target}")],
                [B("⬅️ Utilisateurs", "adm:u:0")]])


async def screen_global_stats(app, update):
    accs = app.store.accounts()
    day = app.store.all_trades(time.time() - 86400)
    every = app.store.all_trades()
    lines = [f"📊 <b>Stats globales</b>\n\nComptes : {len(accs)} · 💎 en circulation : {sum(a['tokens'] for a in accs.values())}"]
    for title, rows in (("24 h", day), ("Total", every)):
        if not rows:
            lines.append(f"\n<b>{title}</b> : aucun trade")
            continue
        w = sum(1 for r in rows if r["outcome"] == "win")
        l = sum(1 for r in rows if r["outcome"] == "loss")
        wr = f"{100 * w / (w + l):.0f}%" if (w + l) else "—"
        lines.append(f"\n<b>{title}</b> : {len(rows)} trades · ✅ {w} · ❌ {l} · WR {wr} · "
                     f"P&amp;L {views.money(sum(r['profit'] for r in rows), True)}")
        for k in REGISTRY:
            sub = [r for r in rows if r["strategy"] == k]
            if sub:
                sw = sum(1 for r in sub if r["outcome"] == "win")
                sl = sum(1 for r in sub if r["outcome"] == "loss")
                lines.append(f"   • {k} : {len(sub)} · WR {(100 * sw / (sw + sl)) if (sw + sl) else 0:.0f}%")
    await show(update, "\n".join(lines), [[B("⬅️ Admin", "adm")]])


def parse_add(text: str):
    """« 123456789 » ou « 123456789 25 » -> (id, jetons)."""
    parts = text.replace(",", " ").split()
    if not parts or not parts[0].lstrip("-").isdigit():
        raise ValueError("Envoyez l'ID Telegram de la personne (ex. 123456789) ou « ID jetons » (ex. 123456789 25)")
    tokens = 0
    if len(parts) > 1:
        try:
            tokens = int(parts[1])
        except ValueError:
            raise ValueError("Le nombre de jetons doit être un entier")
    return int(parts[0]), tokens


async def notify_user(app, uid: int, text: str, rows=None) -> None:
    try:
        await app.bot.send_message(uid, text, parse_mode="HTML", reply_markup=M(rows) if rows else None)
    except TelegramError:
        pass


# ==================================================================== partage du total (image + bouton 🚀)
SPANS = {"today": "Aujourd'hui", "week": "Ces 7 derniers jours"}


def _since(span: str) -> float:
    if span == "week":
        return time.time() - 7 * 86400
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def share_totals(app, span: str) -> dict:
    """Chiffres RÉELS du journal des trades : résultat NET de tous les utilisateurs (gagnants ET perdants, démo + réel)."""
    rows = app.store.all_trades(_since(span))
    net = round(sum(r.get("profit", 0.0) for r in rows), 2)
    return {"net": net, "n": len(rows), "users": len({r["uid"] for r in rows}),
            "demo": round(sum(r.get("profit", 0.0) for r in rows if r.get("demo")), 2),
            "real": round(sum(r.get("profit", 0.0) for r in rows if not r.get("demo")), 2)}


def share_caption(app, span: str, t: dict) -> str:
    amount = views.money(t["net"], True).replace(".00", "").replace(",", " ")
    return texts.get("share", period=SPANS[span], amount=amount, users=t["users"], trades=t["n"])


def start_button(app):
    return [U("🚀 Commencer", f"https://t.me/{app.bot_username}?start=share")] if app.bot_username else []


async def screen_share(app, update):
    await show(update, "🖼 <b>Partager le total des utilisateurs</b>\n\nChoisissez la période : l'image est générée à partir des "
                       "résultats réels enregistrés, puis vous la prévisualisez avant de la publier.",
               [[B("Aujourd'hui", "adm:sh:today"), B("7 jours", "adm:sh:week")], [B("⬅️ Admin", "adm")]])


async def share_generate(app, update, span: str):
    uid = update.effective_user.id
    t = share_totals(app, span)
    if t["net"] <= 0:
        await show(update, f"🖼 {SPANS[span]} : résultat net des utilisateurs <b>{views.money(t['net'], True)}</b> "
                           f"({t['n']} trades).\n\nRien à partager pour le moment : l'image n'affiche que des chiffres réels et positifs.",
                   [[B("🔄 Autre période", "adm:sh"), B("⬅️ Admin", "adm")]])
        return
    bg = visuals.pick("share") or visuals.pick("IMG2")
    img = await asyncio.to_thread(visuals.render_total, bg, app.image_handle(), t["net"])
    cap = share_caption(app, span, t)
    app.share[uid] = (img, cap)
    rows = []
    if app.cfg.channel_id:
        rows.append([B("📣 Publier dans le canal", "adm:shpub:ch")])
    rows.append([B("📨 Diffuser aux utilisateurs", "adm:shpub:bc")])
    rows.append([B("🔄 Autre fond", f"adm:sh:{span}"), B("⬅️ Admin", "adm")])
    chat, q = update.effective_chat, update.callback_query
    preview = (f"\n\n<i>Détail : démo {views.money(t['demo'], True)} · réel {views.money(t['real'], True)}</i>")
    msg = await chat.send_photo(img, caption=cap + preview, parse_mode="HTML", reply_markup=M(rows))
    if q:
        await drop(q.message)
    PANELS[chat.id] = msg


async def share_publish(app, update, target: str):
    uid = update.effective_user.id
    item = app.share.get(uid)
    if not item:
        await show(update, "⌛ Aperçu expiré : régénérez l'image.", [[B("🖼 Partager", "adm:sh"), B("⬅️ Admin", "adm")]])
        return
    img, cap = item
    kb = M([start_button(app)]) if start_button(app) else None
    if target == "ch":
        dests = [app.cfg.channel_id]
    else:
        dests = [u for u in set(app.store.accounts()) | set(app.cfg.allowed_user_ids) if app.is_allowed(u) and u != uid]
    ok = 0
    for d in dests:
        try:
            await app.bot.send_photo(d, img, caption=cap, parse_mode="HTML", reply_markup=kb)
            ok += 1
        except TelegramError:
            pass
        if target != "ch":
            await asyncio.sleep(0.06)
    await show(update, f"{'📣 Publié dans le canal' if target == 'ch' else '📨 Envoyé'} : <b>{ok}</b>/{len(dests)}"
                       + ("" if ok or target != "ch" else "\n⚠️ Le bot doit être administrateur du canal."),
               [[B("⬅️ Admin", "adm")]])


# ==================================================================== réglages, textes et images modifiables depuis Telegram
async def screen_cfg(app, update, note: str = ""):
    rows, row = [], []
    for key, (label, _, _, _) in RS.FIELDS.items():
        row.append(B(f"{label} · {RS.display(app.cfg, key, short=True)}"[:60], f"adm:cfg:{key}"))
        if len(row) == 1:
            rows.append(row)
            row = []
    rows.append([B("⬅️ Admin", "adm")])
    await show(update, (f"{note}\n\n" if note else "") + "⚙️ <b>Réglages</b>\n\nTouchez un réglage pour le modifier. Les changements "
               "s'appliquent tout de suite et sont conservés (même après un redémarrage ou sur le serveur).", rows)


def cfg_prompt(app, key: str) -> str:
    label, kind, secret, helptext = RS.FIELDS[key]
    return (f"⚙️ <b>{E(label)}</b>\n\nValeur actuelle : <code>{E(RS.display(app.cfg, key))}</code>\n"
            + (f"{E(helptext)}\n" if helptext else "")
            + ("\nRépondez <code>oui</code> ou <code>non</code>." if kind == "bool" else
               "\nEnvoyez la nouvelle valeur (ou <code>-</code> pour la vider).")
            + ("\n🔒 Votre message sera supprimé aussitôt." if secret else ""))


async def screen_texts(app, update):
    rows = [[B(("✏️ " if texts.override(k) else "") + label, f"adm:txt:{k}")] for k, (label, _, _) in texts.REGISTRY.items()]
    rows.append([B("⬅️ Admin", "adm")])
    await show(update, "✏️ <b>Textes du bot</b>\n\nChoisissez le texte à modifier (✏️ = déjà personnalisé). "
                       "Vous pouvez mettre du <b>gras</b>/<i>italique</i> avec la mise en forme de Telegram.", rows)


async def screen_text(app, update, key: str, note: str = ""):
    label, variables, default = texts.REGISTRY[key]
    current = texts.override(key) or (default if key != "faq" else "(contenu du fichier faq.txt)")
    vars_line = ("Variables : " + " ".join(f"<code>{{{v}}}</code>" for v in variables) + "\n\n") if variables else ""
    await show(update, (f"{note}\n\n" if note else "") + f"<b>{E(label)}</b>\n\n" + vars_line
               + "Texte actuel :\n<pre>" + E(current[:2500]) + "</pre>",
               [[B("✏️ Modifier", f"adm:txt:{key}:edit")]]
               + ([[B("↩️ Rétablir le texte d'origine", f"adm:txt:{key}:reset")]] if texts.override(key) else [])
               + [[B("⬅️ Textes", "adm:txt")]])


def text_prompt(key: str) -> str:
    label, variables, _ = texts.REGISTRY[key]
    return (f"✏️ <b>{E(label)}</b>\n\nEnvoyez le nouveau texte."
            + (("\nVariables possibles : " + " ".join(f"<code>{{{v}}}</code>" for v in variables)) if variables else "")
            + ("\nTexte simple (sans mise en forme)." if key == "faq" else ""))


async def screen_images(app, update, note: str = ""):
    rows = []
    for name, (label, _) in visuals.SCREENS.items():
        custom = visuals.CUSTOM_DIR is not None and any((visuals.CUSTOM_DIR / f"{name}{e}").is_file() for e in (".jpg", ".jpeg", ".png"))
        mark = "🖼" if custom else ("✅" if visuals.banner_path(name) else "➖")
        rows.append([B(f"{mark} {label}", f"adm:img:{name}")])
    rows.append([B("⬅️ Admin", "adm")])
    await show(update, (f"{note}\n\n" if note else "") + "🖼 <b>Images des écrans</b>\n\nChaque écran peut avoir une image au-dessus du texte. "
               "✅ = image d'origine · 🖼 = la vôtre · ➖ = aucune.\nTouchez un écran puis envoyez la photo.", rows, banner="")


def image_prompt(name: str) -> str:
    label, where = visuals.SCREENS[name]
    return (f"🖼 <b>{E(label)}</b> — {E(where)}\n\nEnvoyez maintenant la photo (ou le fichier image). "
            f"Elle sera mise au format {visuals.BANNER_SIZE[0]}×{visuals.BANNER_SIZE[1]} automatiquement.")
