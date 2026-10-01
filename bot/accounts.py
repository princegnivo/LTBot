"""Écrans comptes : jetons, canal principal, amis, support (utilisateurs) et administration (admins)."""
import time
from urllib.parse import quote

from telegram.error import TelegramError

import views
from bot.ui import B, E, U, show
from strategies import REGISTRY

PAGE = 8


# ==================================================================== utilisateurs
def token_label(app, uid: int) -> str:
    return "∞" if app.is_admin(uid) else str(app.store.tokens(uid))


def support_button(app):
    return U("🆘 Support", app.cfg.support_url) if app.cfg.support_url else B("🆘 Support", "sup")


def channel_label(app, uid: int) -> str:
    return "📣 Canal principal" + (" ✅" if app.store.account(uid)["channel_ok"] else "")


def invite_link(app, uid: int) -> str:
    return f"https://t.me/{app.bot_username}?start=ref_{uid}" if app.bot_username else ""


async def screen_tokens(app, update, uid: int):
    c = app.cfg
    rows = [[B(channel_label(app, uid), "chan")] if c.channel_id else [], [B("👥 Amis", "fr")],
            [support_button(app)], [B("⬅️ Menu", "menu")]]
    gains = []
    if c.channel_id:
        gains.append(f"• 📣 Canal principal : <b>+{c.channel_bonus}</b> (une seule fois)")
    gains.append(f"• 👥 Amis : <b>+{c.ref_bonus}</b> par ami invité")
    gains.append("• 🆘 Besoin de plus ? Contactez le support")
    await show(update,
               "💎 <b>Jetons</b>\n\n"
               "Un jeton est le carburant de l'auto-trading : une session complète (démo ou réelle) = "
               f"<b>{c.cost_auto_demo}</b> jeton.\n"
               "• Signaux et trading manuel — <b>gratuits</b>\n"
               "• Pour trader (auto ou manuel) : votre SSID dans ⚙️ Paramètres › 🔑 Mes comptes\n\n"
               f"Vous avez : <b>{token_label(app, uid)}</b> jetons.\n\n<b>🎁 Gagner des jetons</b>\n" + "\n".join(gains),
               [r for r in rows if r])


async def screen_no_tokens(app, update, uid: int):
    await show(update, "💎 <b>Plus de jetons</b>\n\nL'auto-trading consomme 1 jeton par session. "
                       "Les signaux et le trading manuel restent gratuits.\n\nGagnez-en ou demandez-en au support :",
               [[B("💎 Jetons", "tok")], [B("⬅️ Menu", "menu")]])


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
               rows)


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
               "👥 <b>Invitez vos amis</b>\n\n"
               f"Pour chaque ami : <b>+{app.cfg.ref_bonus} jetons</b> pour vous.\n\n"
               + (f"Votre lien :\n<code>{E(link)}</code>\n\n" if link else "Lien indisponible pour l'instant.\n\n")
               + f"Invités : <b>{len(acc['invited'])}</b> · gagné : <b>{acc['ref_earned']}</b> jetons",
               ([[U("📤 Partager", share)]] if share else []) + [[B("⬅️ Menu", "menu")]])


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
    await show(update,
               "🛠 <b>Administration</b>\n\n"
               f"Comptes : <b>{len(accs)}</b> · avec accès : <b>{n_ok}</b>\n"
               f"💎 Jetons en circulation : <b>{sum(a['tokens'] for a in accs.values())}</b>",
               [[B("👥 Utilisateurs", "adm:u:0"), B("➕ Ajouter", "adm:add")],
                [B("📊 Stats globales", "adm:stats"), B("📣 Diffusion", "adm:bc")],
                [B("⬅️ Menu", "menu")]])


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
               f"👥 Invités : {len(a['invited'])} (+{a['ref_earned']} 💎)\n"
               f"📈 Trades : {len(trades)} · ✅ {wins} · ❌ {loss} · P&amp;L {views.money(pnl, True)}",
               [[B("➕ 10", f"adm:tk:{target}:10"), B("➕ 50", f"adm:tk:{target}:50"), B("➕ 100", f"adm:tk:{target}:100")],
                [B("✏️ Ajouter / retirer…", f"adm:tke:{target}")],
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


async def notify_user(app, uid: int, text: str) -> None:
    try:
        await app.bot.send_message(uid, text, parse_mode="HTML")
    except TelegramError:
        pass
