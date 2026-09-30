"""Bot Telegram (python-telegram-bot v21+) : menus à boutons, tableau de bord en direct, réglages."""
import asyncio
import copy
import html
import json
import logging
import re
import time
from dataclasses import asdict
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup as M, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters)

import analysis
import views
from broker import make_broker
from config import Config
from engine import Engine
from store import Store, UserSettings
from strategies import REGISTRY

log = logging.getLogger("bot")


def B(text: str, data: str) -> InlineKeyboardButton:
    """Bouton à callback (le 2e argument positionnel de InlineKeyboardButton est `url`, d'où le mot-clé)."""
    return InlineKeyboardButton(text, callback_data=data)
E = html.escape
MODE_LABEL = {"demo": "🟠 DÉMO", "real": "🟢 RÉEL"}
CATS = [("currency", "💱 Devises"), ("other", "📈 Actions, indices, matières"), ("crypto", "₿ Crypto")]
EXPS = [5, 15, 30, 60, 120, 300]

# Clavier permanent (sous la zone de saisie) : accès direct, dont « Arrêter »
REPLY_KB = ReplyKeyboardMarkup(
    [["🚀 Auto-trading", "📡 Signaux"], ["🎮 Manuel", "⏹ Arrêter"], ["📊 Compte", "⚙️ Paramètres"]],
    resize_keyboard=True, is_persistent=True, input_field_placeholder="Menu rapide")
KEY_ROUTES = {"🚀 Auto-trading": ["auto"], "📡 Signaux": ["sig"], "🎮 Manuel": ["man"],
              "⏹ Arrêter": ["run", "stop"], "📊 Compte": ["acc"], "⚙️ Paramètres": ["set"]}

# clé: (libellé, type, presets, min, max)
SPEC = {
    "stake": ("Mise ($)", "float", [1, 2, 5, 10, 25], 1, 5000),
    "min_confidence": ("Confiance min (%)", "int", [50, 60, 70, 80, 90], 0, 100),
    "speed": ("⚡ Vitesse d'entrée", "choice", ["ultra", "rapide", "normal", "cloture"], 0, 0),
    "trend_filter": ("Filtre anti contre-tendance", "bool", None, 0, 0),
    "market": ("Marché", "choice", ["otc", "real"], 0, 0),
    "min_payout": ("Paiement min (%)", "int", [85, 90, 92], 0, 100),
    "currencies_only": ("💱 Devises uniquement (auto)", "bool", None, 0, 0),
    "scan_assets": ("Actifs surveillés (auto)", "int", [1, 2, 3, 5, 8], 1, 10),
    "mg_enabled": ("Martingale (stratégie 5s)", "bool", None, 0, 0),
    "mg_steps": ("Étapes de martingale (5s)", "int", [1, 2, 3, 4, 5], 1, 6),
    "mg_method": ("Méthode martingale", "choice", ["recovery", "multiplier"], 0, 0),
    "mg_multiplier": ("Multiplicateur (méthode ×)", "float", [2, 2.2, 2.5, 3], 1.1, 5),
    "series_deals": ("Deals par série", "int", [3, 5, 10, 20], 1, 100),
    "take_profit": ("Take-profit session ($, 0 = off)", "float", [5, 10, 20, 50, 100], 0, 1e6),
    "stop_loss": ("Stop-loss session ($, 0 = off)", "float", [10, 20, 30, 50, 100], 0, 1e6),
    "max_consec_losses": ("Deals perdus d'affilée max (0 = off)", "int", [0, 2, 3, 5], 0, 50),
    "max_open": ("Positions simultanées max", "int", [1, 2, 3, 5, 8], 1, 20),
    "max_open_per_asset": ("Positions max par actif", "int", [1, 2, 3], 1, 10),
    "max_exposure_pct": ("Exposition max (% du solde)", "float", [10, 20, 30, 50], 1, 100),
    "max_step_pct": ("Étape martingale max (% du solde)", "float", [10, 25, 40, 60], 1, 100),
    "cooldown_s": ("Délai mini entre entrées (s)", "int", [0, 3, 5, 10, 30], 0, 3600),
    "manual_exp": ("Expiration manuelle (s)", "choice", EXPS, 0, 0),
    "notify_signals": ("Notifier les signaux", "bool", None, 0, 0),
    "notify_results": ("Notifier les résultats", "bool", None, 0, 0),
}
GROUPS = {
    "sig": ("🧠 Stratégies & signaux", ["strategies", "min_confidence", "speed", "trend_filter"]),
    "ast": ("🎯 Actifs", ["market", "currencies_only", "min_payout", "scan_assets"]),
    "stk": ("💵 Mise & martingale (5s)", ["stake", "mg_enabled", "mg_steps", "mg_method", "mg_multiplier", "manual_exp"]),
    "rsk": ("🛡 Risque & positions", ["series_deals", "take_profit", "stop_loss", "max_consec_losses", "max_open",
                                      "max_open_per_asset", "max_exposure_pct", "max_step_pct", "cooldown_s"]),
    "ntf": ("🔔 Notifications", ["notify_signals", "notify_results"]),
}
SPEED_LABEL = {"ultra": "⚡⚡ Ultra", "rapide": "⚡ Rapide", "normal": "Normale", "cloture": "🕯 À la clôture"}
SPEED_DESC = {
    "ultra": "Évalue les stratégies à chaque tick (jusqu'à ~50 fois/s) et entre à la milliseconde où les conditions sont réunies. "
             "Plus de CPU ; un signal peut disparaître avant la fin de la bougie.",
    "rapide": "Évalue ~8 fois/s. Entrée quasi immédiate, charge modérée.",
    "normal": "Évalue ~2 fois/s. Économe, entrée moins précise.",
    "cloture": "N'évalue qu'à la clôture de chaque bougie : signaux stables (pas de repeinture), entrée retardée.",
}
VALUE_FMT = {"speed": SPEED_LABEL, "market": {"otc": "OTC", "real": "Réel"}, "mg_method": {"recovery": "Récupération", "multiplier": "Multiplicateur ×"}}


class Runtime:
    def __init__(self, uid: int):
        self.uid = uid
        self.chat_id: Optional[int] = None
        self.broker = None
        self.broker_mode = ""
        self.engine: Optional[Engine] = None
        self.manual: Optional[Engine] = None
        self.dash: Optional[asyncio.Task] = None
        self.assets_cache = (0.0, [])
        self.last_signal_msg = 0.0


class App:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.rts: Dict[int, Runtime] = {}
        self.bot = None

    def rt(self, uid: int) -> Runtime:
        return self.rts.setdefault(uid, Runtime(uid))

    def settings(self, uid: int) -> UserSettings:
        return self.store.settings(uid)

    # ---------------------------------------------------------------- courtier
    async def ensure_broker(self, rt: Runtime):
        s = self.settings(rt.uid)
        if rt.broker is not None and rt.broker_mode == s.mode:
            if rt.broker.is_connected():
                return rt.broker
            try:
                await asyncio.wait_for(rt.broker.reconnect(), 90)
                return rt.broker
            except Exception as e:
                log.warning("reconnexion impossible (%s) -> recréation du client", e)
        await self.drop_broker(rt)
        if s.mode == "real" and not self.cfg.allow_real:
            raise RuntimeError("Mode RÉEL verrouillé : mettez ALLOW_REAL=1 dans .env pour l'activer.")
        ssid = self.store.get_ssid(rt.uid, s.mode) or (self.cfg.ssid_demo if s.mode == "demo" else self.cfg.ssid_real)
        if not ssid and self.cfg.broker != "paper":
            raise RuntimeError(f"Aucun SSID {s.mode.upper()}. Envoyez : /ssid {s.mode} &lt;votre SSID&gt;")
        b = make_broker(self.cfg.broker, ssid)
        await asyncio.wait_for(b.start(), 90)
        rt.broker, rt.broker_mode = b, s.mode
        return b

    async def drop_broker(self, rt: Runtime):
        if rt.broker is not None:
            try:
                await (rt.broker.shutdown() if hasattr(rt.broker, "shutdown") else rt.broker.stop())
            except Exception:
                pass
        rt.broker, rt.broker_mode, rt.manual = None, "", None

    def stake_cap(self, s: UserSettings) -> Optional[float]:
        return self.cfg.max_real_stake if s.mode == "real" else None

    # ---------------------------------------------------------------- actifs
    async def assets(self, rt: Runtime):
        ts, cached = rt.assets_cache
        if time.time() - ts < 20 and cached:
            return cached
        b = await self.ensure_broker(rt)
        data = await b.assets()
        rt.assets_cache = (time.time(), data)
        return data

    async def top_assets(self, rt: Runtime, market: str, cat: str, n: int = 6):
        minp = self.settings(rt.uid).min_payout
        rows = [a for a in await self.assets(rt) if a.active and a.is_otc == (market == "otc") and a.category == cat
                and a.payout >= minp]
        rows.sort(key=lambda a: -a.payout)
        hist: Dict[str, List[int]] = {}
        for t in self.store.trades(rt.uid):
            if t["outcome"] in ("win", "loss"):
                h = hist.setdefault(t["asset"], [0, 0])
                h[0 if t["outcome"] == "win" else 1] += 1
        out = []
        for a in rows[:n]:
            w, l = hist.get(a.symbol, [0, 0])
            wr = f" · WR {100 * w / (w + l):.0f}%" if w + l >= 5 else ""
            out.append((a, f"{a.name} · {a.payout}%{wr}"))
        return out

    # ---------------------------------------------------------------- envoi
    async def send(self, chat_id: int, text: str, rows=None):
        try:
            return await self.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=M(rows) if rows else None)
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.5)
            return await self.send(chat_id, text, rows)
        except TelegramError as e:
            log.warning("send: %s", e)

    # ---------------------------------------------------------------- session
    def session_notify(self, rt: Runtime):
        async def notify(kind, engine, **d):
            chat = rt.chat_id
            if not chat:
                return
            if kind == "signal":
                if time.time() - rt.last_signal_msg < 1.2:     # anti-flood Telegram
                    return
                rt.last_signal_msg = time.time()
                await self.send(chat, views.signal_text(d))
            elif kind in ("info", "error"):
                await self.send(chat, d["text"])
            elif kind == "session_end":
                text = views.session_summary(engine) if engine.kind == "auto" else \
                    f"📡 Scan arrêté · {engine.signals_seen} signaux détectés."
                await self.send(chat, text, [[B("▶️ Relancer", "auto" if engine.kind == "auto" else "sig"),
                                              B("⬅️ Menu", "menu")]])
        return notify

    def manual_notify(self, rt: Runtime):
        async def notify(kind, engine, **d):
            chat = rt.chat_id
            if not chat:
                return
            if kind == "position_open":
                p, deal = d["pos"], d["deal"]
                em, lab = views.DIR[p.direction]
                await self.send(chat, f"⚡ Ordre passé en {p.latency_ms:.0f} ms : {em} <b>{lab}</b> {views.money(p.amount)} · "
                                      f"{E(engine.names.get(p.asset, p.asset))} · {views.exp_label(p.expiration)}\n⏱ résultat à l'expiration…")
            elif kind == "position_closed":
                p = d["pos"]
                icon = {"win": "✅ Gagné", "loss": "❌ Perdu", "draw": "⚪ Égalité"}[p.status]
                await self.send(chat, f"{icon} <b>{views.money(p.profit, True)}</b> · {E(engine.names.get(p.asset, p.asset))}",
                                [[B("🎮 Nouveau trade", "man"), B("⬅️ Menu", "menu")]])
            elif kind == "error":
                await self.send(chat, "⚠️ " + d["text"])
        return notify

    async def launch(self, rt: Runtime, execute: bool) -> str:
        if rt.engine and rt.engine.running:
            return "Une session est déjà en cours."
        s = self.settings(rt.uid)
        cap = self.stake_cap(s)
        if execute and cap is not None and s.stake > cap:
            return f"Mise ${s.stake:g} > plafond RÉEL ${cap:g} (MAX_REAL_STAKE)."
        b = await self.ensure_broker(rt)
        eng = Engine(rt.uid, s, b, self.store, self.session_notify(rt), stake_cap=cap)
        await eng.start(execute)
        rt.engine = eng
        msg = await self.send(rt.chat_id, views.dashboard(eng), [[B("⏹ Arrêter", "run:stop")]])
        if msg is not None:
            rt.dash = asyncio.create_task(self.dash_loop(eng, msg))
        return ""

    async def dash_loop(self, eng: Engine, msg):
        """Rafraîchit le tableau de bord dès qu'un événement survient (plus de délai fixe), au plus ~3 fois/s ;
        le décompte de la position en cours se met à jour toutes les 1,5 s."""
        last = None
        while True:
            try:
                await asyncio.wait_for(eng.changed.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                pass
            eng.changed.clear()
            done = not eng.running
            text = views.dashboard(eng)
            if text != last:
                try:
                    await msg.edit_text(text, parse_mode="HTML",
                                        reply_markup=None if done else M([[B("⏹ Arrêter", "run:stop")]]))
                    last = text
                except RetryAfter as e:
                    await asyncio.sleep(float(e.retry_after) + 0.3)
                    eng.changed.set()
                except BadRequest:
                    last = text
                except TelegramError as e:
                    log.warning("dash: %s", e)
            if done:
                return
            await asyncio.sleep(0.35)

    async def shutdown(self):
        for rt in self.rts.values():
            if rt.engine:
                await rt.engine.shutdown()
            await self.drop_broker(rt)


# ==================================================================== UI
def kb_main(app: App, uid: int):
    s = app.settings(uid)
    other = "🔒 Passer en RÉEL" if s.mode == "demo" else "🟠 Revenir en DÉMO"
    return [[B("🚀 Lancer l'auto-trading", "auto")],
            [B("📡 Signaux seuls", "sig"), B("🎮 Mode manuel", "man")],
            [B(other, "mode")],
            [B("📊 Mon compte", "acc"), B("📈 Stats", "acc:stats:today")],
            [B("⚙️ Paramètres", "set"), B("❓ Aide", "help")]]


def main_text(app: App, uid: int) -> str:
    s = app.settings(uid)
    rt = app.rt(uid)
    run = "\n🟢 Session en cours" if rt.engine and rt.engine.running else ""
    return (f"👋 <b>Bon retour !</b>\n\n{MODE_LABEL[s.mode]} · mise ${s.stake:g} · "
            f"stratégies {', '.join(s.strategies)}{run}")


async def show(update: Update, text: str, rows):
    q = update.callback_query
    markup = M(rows)
    try:
        if q:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        else:
            await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            await update.effective_chat.send_message(text, parse_mode="HTML", reply_markup=markup)


def fmt_val(s: UserSettings, key: str) -> str:
    v = getattr(s, key)
    kind = SPEC[key][1]
    if kind == "bool":
        return "✅" if v else "❌"
    if key in VALUE_FMT:
        return VALUE_FMT[key].get(v, str(v))
    return f"{v:g}" if isinstance(v, float) else str(v)


def kb_group(s: UserSettings, gid: str):
    rows = []
    for key in GROUPS[gid][1]:
        if key == "strategies":
            rows.append([B(f"Stratégies : {', '.join(s.strategies)}", "set:st")])
        else:
            kind = SPEC[key][1]
            act = "set:sp" if key == "speed" else f"set:t:{key}" if kind in ("bool", "choice") else f"set:k:{key}"
            rows.append([B(f"{SPEC[key][0]} : {fmt_val(s, key)}", act)])
    rows.append([B("⬅️ Paramètres", "set")])
    return rows


# ---------------------------------------------------------------- écrans
async def screen_menu(app: App, update: Update, uid: int):
    await show(update, main_text(app, uid), kb_main(app, uid))


async def screen_stake(app: App, update: Update, uid: int):
    s = app.settings(uid)
    rows = [[B(f"${v:g}", f"auto:stake:{v:g}") for v in (1, 2, 5, 10)],
            [B("✏️ Autre montant", "auto:stake:edit")],
            [B("🔒 Passer en RÉEL" if s.mode == "demo" else "🟠 Revenir en DÉMO", "mode")],
            [B("⬅️ Menu", "menu")]]
    await show(update, f"Choisissez votre mise de départ :\n\nLa session tournera sur : <b>{MODE_LABEL[s.mode]}</b>", rows)


async def screen_asset_scope(update: Update, kind: str):
    await show(update, "🎯 <b>Actif</b>\n\nChoisir l'actif automatiquement (le bot surveille les paires au meilleur paiement "
                       "et entre dès qu'un signal est validé) ou manuellement ?",
               [[B("🎯 Sélection auto", f"{kind}:as:auto")], [B("🔧 Choisir manuellement", f"{kind}:as:man")],
                [B("⬅️ Menu", "menu")]])


async def screen_market(update: Update, kind: str):
    await show(update, "🎮 <b>Marché</b> :", [[B("📊 OTC", f"{kind}:mk:otc"), B("🌍 Réel", f"{kind}:mk:real")],
                                             [B("⬅️ Menu", "menu")]])


async def screen_cats(update: Update, kind: str, market: str):
    await show(update, f"{'📊 OTC' if market == 'otc' else '🌍 Réel'}\n\nCatégorie :",
               [[B(lbl, f"{kind}:ct:{market}:{c}")] for c, lbl in CATS] + [[B("⬅️ Retour", f"{kind}:as:man")]])


async def screen_top(app: App, update: Update, rt: Runtime, kind: str, market: str, cat: str):
    top = await app.top_assets(rt, market, cat)
    lbl = dict(CATS)[cat]
    if not top:
        await show(update, f"{lbl}\n\nAucun actif à {app.settings(rt.uid).min_payout}% ou plus pour le moment.", [[B("⬅️ Retour", f"{kind}:mk:{market}")]])
        return
    rows = [[B(t, f"{kind}:pk:{a.symbol}")] for a, t in top]
    rows.append([B("⬅️ Retour", f"{kind}:mk:{market}")])
    await show(update, f"📊 <b>{'OTC' if market == 'otc' else 'Réel'} · {lbl}</b>\nTop 6 par paiement "
                       f"(winrate à côté, le cas échéant) :", rows)


async def screen_auto_mode(app: App, update: Update, uid: int):
    s = app.settings(uid)
    await show(update,
               "🤖 <b>Mode auto-trading</b>\n\n"
               f"🎯 <b>Série de {s.series_deals} deals</b> — chaque deal se ferme dès qu'il est en profit "
               f"(martingale sur la stratégie 5s uniquement).\n\n"
               "📈 <b>Par take-profit</b> — trade jusqu'au profit ou à la perte que vous fixez à l'étape suivante, "
               "sans limite de deals.\n\n"
               f"🛡 Positions simultanées : {s.max_open} · vitesse : {SPEED_LABEL.get(s.speed, s.speed)}",
               [[B(f"🎯 Série ({s.series_deals} deals)", "auto:md:series")], [B("📈 Par take-profit", "auto:md:tp")],
                [B("⚙️ Réglages", "set"), B("⬅️ Menu", "menu")]])


async def screen_strategies(app: App, update: Update, uid: int, kind: str):
    s = app.settings(uid)
    rows = [[B(("✅ " if k in s.strategies else "⬜ ") + REGISTRY[k].label, f"{kind}:st:t:{k}")] for k in REGISTRY]
    rows.append([B("Toutes", f"{kind}:st:all"), B("▶️ Valider", f"{kind}:st:ok")])
    rows.append([B("⬅️ Menu", "menu")])
    await show(update, "🧠 <b>Stratégies</b>\n\nTouchez pour choisir <b>5s</b>, <b>1M</b> et/ou <b>2M</b>, puis validez.\n"
                       "La martingale ne s'applique qu'à la stratégie 5s.", rows)


async def screen_tp(update: Update):
    await show(update, "🎯 <b>Take-profit</b>\n\nGain de session à partir duquel le bot s'arrête (plus aucune nouvelle position) :",
               [[B(f"+${v}", f"auto:tp:{v}") for v in (5, 10, 20, 50)],
                [B("✏️ Autre montant", "auto:tp:edit"), B("🚫 Aucun", "auto:tp:0")], [B("⬅️ Menu", "menu")]])


async def screen_sl(update: Update, tp: float):
    tps = f"+${tp:g}" if tp > 0 else "aucun"
    await show(update, f"🛑 <b>Stop-loss</b>\n\nTake-profit : <b>{tps}</b>\nPerte de session à partir de laquelle le bot s'arrête :",
               [[B(f"−${v}", f"auto:sl:{v}") for v in (10, 20, 30, 50)],
                [B("✏️ Autre montant", "auto:sl:edit"), B("🚫 Aucun", "auto:sl:0")], [B("⬅️ Menu", "menu")]])


async def screen_manual(app: App, update: Update, rt: Runtime, ctx: ContextTypes.DEFAULT_TYPE):
    s = app.settings(rt.uid)
    asset = ctx.user_data.get("man_asset")
    b = await app.ensure_broker(rt)
    names = {a.symbol: a.name for a in await app.assets(rt)}
    pay = {a.symbol: a.payout for a in await app.assets(rt)}
    tr = {"dir": "neutral", "label": "indisponible", "text": ""}
    try:
        async def first():
            async for closed, forming in b.candles(asset, 60):
                return closed + ([forming] if forming else [])
        rows_c = await asyncio.wait_for(first(), 15)
        tr = analysis.trend(Engine._df(rows_c))
    except Exception as e:
        log.warning("analyse manuelle: %s", e)
    reco = {"up": "recommandé 🟢 HAUSSE", "down": "recommandé 🔴 BAISSE", "neutral": "pas de recommandation nette"}.get(tr["dir"], "")
    exp_row = [B(("✔ " if s.manual_exp == e else "") + views.exp_label(e), f"man:exp:{e}") for e in EXPS]
    stk_row = [B(("✔ " if abs(s.stake - v) < 1e-9 else "") + f"${v:g}", f"man:stk:{v:g}") for v in (1, 2, 5, 10)]
    await show(update,
               f"📊 <b>{E(names.get(asset, asset))}</b> · paiement {pay.get(asset, '?')}%\n"
               f"Analyse (60s) : tendance <b>{tr['label']}</b> {tr['text']} → {reco}\n"
               f"Mise ${s.stake:g} · expiration {views.exp_label(s.manual_exp)} · {MODE_LABEL[s.mode]}\n\nChoisissez le sens :",
               [[B("🟢 HAUSSE (call)", "man:go:call"), B("🔴 BAISSE (put)", "man:go:put")],
                exp_row[:3], exp_row[3:], stk_row, [B("⬅️ Menu", "menu")]])


async def screen_account(app: App, update: Update, rt: Runtime):
    s = app.settings(rt.uid)
    b = await app.ensure_broker(rt)
    bal = await b.balance()
    txt = (f"📊 <b>Mon compte</b>\n\n{MODE_LABEL[s.mode]}\n💰 Solde {'démo' if s.mode == 'demo' else 'réel'} : "
           f"<b>{views.money(bal)}</b>\n🔌 Connexion : {'✅' if b.is_connected() else '❌'}")
    if s.mode == "real":
        txt += f"\n🛡 Plafond de mise réel : ${app.cfg.max_real_stake:g}"
    await show(update, txt, [[B("📈 Stats", "acc:stats:today")], [B("🔁 Changer de mode", "mode")], [B("⬅️ Menu", "menu")]])


async def screen_stats(app: App, update: Update, uid: int, span: str):
    since = time.time() - 86400 if span == "today" else 0.0
    s = app.settings(uid)
    rows = [t for t in app.store.trades(uid, since) if t.get("demo", True) == (s.mode == "demo")]
    title = f"Stats {'24 h' if span == 'today' else 'totales'} · {MODE_LABEL[s.mode]}"
    other = ("Tout", "acc:stats:all") if span == "today" else ("24 h", "acc:stats:today")
    await show(update, views.stats_text(rows, title), [[B(other[0], other[1])], [B("⬅️ Menu", "menu")]])


HELP = ("❓ <b>Aide</b>\n\n"
        "• <b>Auto-trading</b> : le bot surveille les devises à 92 %, entre <i>dès que</i> les conditions d'une stratégie sont "
        "réunies (5s, 1M, 2M ; martingale sur 5s seulement) et peut ouvrir plusieurs positions en parallèle.\n"
        "• <b>Signaux seuls</b> : alertes sans aucun ordre.\n"
        "• <b>Manuel</b> : vous choisissez l'actif, le sens, la mise et l'expiration.\n"
        "• Clavier sous la zone de saisie : accès direct, dont <b>⏹ Arrêter</b> (coupe signaux ou auto-trading).\n\n"
        "🛡 <b>Seuils</b> : take-profit, stop-loss, pertes d'affilée, exposition et taille d'étape agissent sur la session. "
        "Une option binaire ne peut pas être clôturée avant son expiration : au seuil, le bot n'ouvre plus rien et "
        "laisse les positions en cours aller à terme.\n\n"
        "⚠️ La martingale augmente fortement le risque. Testez toujours en DÉMO.\n"
        "/stop coupe la session · /ssid demo|real &lt;SSID&gt; enregistre votre session · /id affiche votre ID Telegram.")


# ==================================================================== routage
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    app: App = ctx.application.bot_data["app"]
    q = update.callback_query
    uid = update.effective_user.id
    if uid not in app.cfg.allowed_user_ids:
        await q.answer("Accès refusé", show_alert=True)
        return
    await q.answer()
    rt = app.rt(uid)
    rt.chat_id = update.effective_chat.id
    p = q.data.split(":")
    try:
        await route(app, update, ctx, rt, p)
    except Exception as e:
        log.exception("callback %s", q.data)
        await show(update, f"⚠️ {E(str(e)) or 'Erreur'}", [[B("⬅️ Menu", "menu")]])


async def route(app: App, update: Update, ctx, rt: Runtime, p: List[str]):
    uid, s = rt.uid, app.settings(rt.uid)
    head = p[0]
    ctx.user_data.pop("await", None)

    if head == "menu":
        await screen_menu(app, update, uid)
    elif head == "help":
        await show(update, HELP, [[B("⬅️ Menu", "menu")]])
    elif head == "noop":
        return

    # ---- auto-trading -------------------------------------------------------
    elif head == "auto" and len(p) == 1:
        await screen_stake(app, update, uid)
    elif head == "sig" and len(p) == 1:
        await screen_asset_scope(update, "sig")
    elif head == "man" and len(p) == 1:
        await screen_market(update, "man")
    elif head == "auto" and p[1] == "stake":
        if p[2] == "edit":
            ctx.user_data["await"] = ("stake", "auto")
            await show(update, "✏️ Envoyez le montant de la mise (ex. 3.5) :", [[B("⬅️ Annuler", "auto")]])
        else:
            s.stake = float(p[2]); app.store.save_settings(uid, s)
            await screen_asset_scope(update, "auto")
    elif head in ("auto", "sig") and p[1] == "as":
        if p[2] == "auto":
            s.asset_mode = "auto"; app.store.save_settings(uid, s)
            await screen_strategies(app, update, uid, head)
        else:
            await screen_market(update, head)
    elif head in ("auto", "sig", "man") and p[1] == "mk":
        await screen_cats(update, head, p[2])
    elif head in ("auto", "sig", "man") and p[1] == "ct":
        await screen_top(app, update, rt, head, p[2], p[3])
    elif head in ("auto", "sig") and p[1] == "pk":
        s.asset_mode, s.asset = "manual", ":".join(p[2:]); app.store.save_settings(uid, s)
        await screen_strategies(app, update, uid, head)
    elif head in ("auto", "sig") and p[1] == "st":
        if p[2] == "t":
            cur = list(s.strategies)
            if p[3] in cur and len(cur) > 1:
                cur.remove(p[3])
            elif p[3] not in cur:
                cur.append(p[3])
            s.strategies = [k for k in REGISTRY if k in cur]
            app.store.save_settings(uid, s)
            await screen_strategies(app, update, uid, head)
        elif p[2] == "all":
            s.strategies = list(REGISTRY); app.store.save_settings(uid, s)
            await screen_strategies(app, update, uid, head)
        else:                                               # ok
            await (screen_auto_mode(app, update, uid) if head == "auto"
                   else launch_and_report(app, update, rt, False))
    elif head == "auto" and p[1] == "md":
        s.session_mode = p[2]; app.store.save_settings(uid, s)
        await screen_tp(update)
    elif head == "auto" and p[1] == "tp":
        if p[2] == "edit":
            ctx.user_data["await"] = ("tp", "auto")
            await show(update, "✏️ Envoyez le take-profit en $ (ex. 15) :", [[B("⬅️ Annuler", "menu")]])
        else:
            s.take_profit = float(p[2]); app.store.save_settings(uid, s)
            await screen_sl(update, s.take_profit)
    elif head == "auto" and p[1] == "sl":
        if p[2] == "edit":
            ctx.user_data["await"] = ("sl", "auto")
            await show(update, "✏️ Envoyez le stop-loss en $ (ex. 25) :", [[B("⬅️ Annuler", "menu")]])
        else:
            s.stop_loss = float(p[2]); app.store.save_settings(uid, s)
            await launch_and_report(app, update, rt, True)

    # ---- manuel -------------------------------------------------------------------
    elif head == "man" and p[1] == "pk":
        ctx.user_data["man_asset"] = ":".join(p[2:])
        await screen_manual(app, update, rt, ctx)
    elif head == "man" and p[1] in ("exp", "stk"):
        if p[1] == "exp":
            s.manual_exp = int(p[2])
        else:
            s.stake = float(p[2])
        app.store.save_settings(uid, s)
        await screen_manual(app, update, rt, ctx)
    elif head == "man" and p[1] == "go":
        await manual_go(app, update, ctx, rt, p[2])

    # ---- arrêt --------------------------------------------------------------------
    elif head == "run" and p[1] == "stop":
        if rt.engine and rt.engine.running:
            rt.engine.stop("Arrêt manuel")
            await q_toast(update, "Arrêt demandé : plus aucune nouvelle position.")
        else:
            await q_toast(update, "Aucune session en cours.")

    # ---- compte / stats / mode ----------------------------------------------------
    elif head == "acc" and len(p) == 1:
        await screen_account(app, update, rt)
    elif head == "acc" and p[1] == "stats":
        await screen_stats(app, update, uid, p[2])
    elif head == "mode":
        await route_mode(app, update, rt, s, p)

    # ---- paramètres ---------------------------------------------------------------
    elif head == "set":
        await route_settings(app, update, ctx, rt, s, p)


async def q_toast(update: Update, text: str):
    try:
        await update.effective_chat.send_message(text)
    except TelegramError:
        pass


async def launch_and_report(app: App, update: Update, rt: Runtime, execute: bool):
    try:
        err = await app.launch(rt, execute)
    except Exception as e:
        err = str(e) or repr(e)
    if err:
        await show(update, f"⚠️ {err}", [[B("⚙️ Réglages", "set"), B("⬅️ Menu", "menu")]])
    else:
        await show(update, main_text(app, rt.uid), kb_main(app, rt.uid))


async def manual_go(app: App, update: Update, ctx, rt: Runtime, direction: str):
    s = app.settings(rt.uid)
    asset = ctx.user_data.get("man_asset")
    b = await app.ensure_broker(rt)
    if rt.manual is None:
        rt.manual = Engine(rt.uid, s, b, app.store, app.manual_notify(rt))
        await rt.manual.prepare()
    m = rt.manual
    m.cfg, m.stake_cap = copy.deepcopy(s), app.stake_cap(s)
    m.balance = await b.balance()
    try:
        await m.manual_trade(asset, direction, s.stake, s.manual_exp)
    except ValueError as e:
        await q_toast(update, f"⛔ {e}")


async def route_mode(app: App, update: Update, rt: Runtime, s: UserSettings, p: List[str]):
    if rt.engine and rt.engine.running:
        await q_toast(update, "Arrêtez la session avant de changer de mode.")
        return
    if s.mode == "real":
        s.mode = "demo"; app.store.save_settings(rt.uid, s)
        await screen_menu(app, update, rt.uid)
        return
    if not app.cfg.allow_real:
        await show(update, "🔒 Le mode RÉEL est verrouillé.\nMettez <code>ALLOW_REAL=1</code> dans <code>.env</code> "
                           "puis redémarrez le bot.", [[B("⬅️ Menu", "menu")]])
    elif len(p) > 1 and p[1] == "ok":
        s.mode = "real"; app.store.save_settings(rt.uid, s)
        await screen_menu(app, update, rt.uid)
    else:
        await show(update, "⚠️ <b>Passer en RÉEL ?</b>\n\nLes ordres utiliseront de l'argent réel. Plafond de mise : "
                           f"${app.cfg.max_real_stake:g}. La martingale peut multiplier les pertes.",
                   [[B("✅ Je confirme", "mode:ok"), B("❌ Annuler", "menu")]])


async def route_settings(app: App, update: Update, ctx, rt: Runtime, s: UserSettings, p: List[str]):
    uid = rt.uid
    if len(p) == 1:
        rows = [[B(lbl, f"set:g:{gid}")] for gid, (lbl, _) in GROUPS.items()] + [[B("⬅️ Menu", "menu")]]
        await show(update, "⚙️ <b>Paramètres</b>", rows)
    elif p[1] == "g":
        await show(update, f"<b>{GROUPS[p[2]][0]}</b>", kb_group(s, p[2]))
    elif p[1] == "st":
        await show(update, "🧠 <b>Stratégies actives</b>\n(touchez pour activer / désactiver)",
                   [[B(("✅ " if k in s.strategies else "❌ ") + REGISTRY[k].label, f"set:s:{k}")] for k in REGISTRY]
                   + [[B("⬅️ Retour", "set:g:sig")]])
    elif p[1] == "s":
        k = p[2]
        cur = list(s.strategies)
        if k in cur and len(cur) > 1:
            cur.remove(k)
        elif k not in cur:
            cur.append(k)
        s.strategies = [x for x in REGISTRY if x in cur]
        app.store.save_settings(uid, s)
        await route_settings(app, update, ctx, rt, s, ["set", "st"])
    elif p[1] == "sp":                                  # sélecteur de vitesse d'entrée
        rows = [[B(("✔ " if s.speed == k else "") + SPEED_LABEL[k], f"set:sv:{k}")] for k in SPEED_LABEL]
        rows.append([B("⬅️ Retour", "set:g:sig")])
        await show(update, "⚡ <b>Vitesse d'entrée</b>\n\n" + "\n\n".join(
            f"<b>{SPEED_LABEL[k]}</b> — {SPEED_DESC[k]}" for k in SPEED_LABEL), rows)
    elif p[1] == "sv":
        if p[2] in SPEED_LABEL:
            s.speed = p[2]; app.store.save_settings(uid, s)
        await route_settings(app, update, ctx, rt, s, ["set", "sp"])
    elif p[1] == "t":                                   # bascule bool / choix suivant
        key = p[2]
        kind, presets = SPEC[key][1], SPEC[key][2]
        if kind == "bool":
            setattr(s, key, not getattr(s, key))
        else:
            i = presets.index(getattr(s, key)) if getattr(s, key) in presets else -1
            setattr(s, key, presets[(i + 1) % len(presets)])
        app.store.save_settings(uid, s)
        gid = next(g for g, (_, ks) in GROUPS.items() if key in ks)
        await show(update, f"<b>{GROUPS[gid][0]}</b>", kb_group(s, gid))
    elif p[1] == "k":                                   # page d'une valeur numérique
        key = p[2]
        lbl, kind, presets, lo, hi = SPEC[key]
        gid = next(g for g, (_, ks) in GROUPS.items() if key in ks)
        rows = [[B(f"{v:g}", f"set:v:{key}:{v:g}") for v in presets], [B("✏️ Saisir", f"set:e:{key}")],
                [B("⬅️ Retour", f"set:g:{gid}")]]
        await show(update, f"<b>{lbl}</b>\nValeur actuelle : <b>{fmt_val(s, key)}</b>", rows)
    elif p[1] == "v":
        apply_value(app, uid, s, p[2], p[3])
        gid = next(g for g, (_, ks) in GROUPS.items() if p[2] in ks)
        await show(update, f"<b>{GROUPS[gid][0]}</b>", kb_group(app.settings(uid), gid))
    elif p[1] == "e":
        ctx.user_data["await"] = ("set", p[2])
        await show(update, f"✏️ Envoyez la nouvelle valeur pour <b>{SPEC[p[2]][0]}</b> :", [[B("⬅️ Annuler", "set")]])


def apply_value(app: App, uid: int, s: UserSettings, key: str, raw: str) -> None:
    lbl, kind, presets, lo, hi = SPEC[key]
    try:
        v = float(raw.strip().replace(",", ".").lstrip("$"))
    except ValueError:
        raise ValueError(f"{lbl} : envoyez un nombre (ex. 10)")
    if not (lo <= v <= hi):
        raise ValueError(f"{lbl} : valeur entre {lo:g} et {hi:g}")
    setattr(s, key, int(v) if kind == "int" else v)
    app.store.save_settings(uid, s)


# ==================================================================== commandes & texte
def guard(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        app: App = ctx.application.bot_data["app"]
        uid = update.effective_user.id
        if uid not in app.cfg.allowed_user_ids:
            await update.effective_message.reply_text(
                "⛔ Accès refusé. Ajoutez votre ID Telegram à ALLOWED_USER_IDS dans .env "
                f"(votre ID : {uid}).")
            return
        app.rt(uid).chat_id = update.effective_chat.id
        await fn(app, update, ctx)
    return wrapper


@guard
async def cmd_start(app: App, update: Update, ctx):
    await update.effective_message.reply_text("⌨️ Raccourcis activés sous le clavier — dont ⏹ Arrêter.", reply_markup=REPLY_KB)
    await screen_menu(app, update, update.effective_user.id)


@guard
async def cmd_stop(app: App, update: Update, ctx):
    rt = app.rt(update.effective_user.id)
    if rt.engine and rt.engine.running:
        rt.engine.stop("Arrêt manuel (/stop)")
        await update.effective_message.reply_text("⏹ Arrêt demandé : plus aucune nouvelle position.")
    else:
        await update.effective_message.reply_text("Aucune session en cours.")


SSID_RE = re.compile(r"^/ssid(?:@\w+)?\s+(?:(demo|real)\s+)?(.+)$", re.I | re.S)


def parse_ssid(raw: str):
    """Nettoie et valide un SSID. Retourne (ssid, is_demo, erreur). Tolère guillemets courbes, sauts de ligne, ```."""
    s = raw.strip().strip("`")
    for bad, good in (("“", '"'), ("”", '"'), ("„", '"'), ("‟", '"'), ("«", '"'), ("»", '"'),
                      ("‘", "'"), ("’", "'"), ("\u00a0", " ")):
        s = s.replace(bad, good)
    s = s.replace("\r", "").replace("\n", "")
    i = s.find("42[")
    if i == -1:
        j = s.find('["auth"')
        if j == -1:
            return "", None, 'il doit ressembler à 42["auth",{...}]'
        s = "42" + s[j:]
    else:
        s = s[i:]
    j = s.rfind("]")
    if j == -1:
        return "", None, "incomplet (il doit finir par ]) — le message a peut-être été coupé"
    s = s[: j + 1]
    try:
        data = json.loads(s[2:])
        assert data[0] == "auth" and isinstance(data[1], dict) and data[1].get("session")
    except Exception:
        return "", None, "contenu illisible ou incomplet (guillemets modifiés ? texte coupé ?)"
    return s, bool(data[1].get("isDemo")), ""


@guard
async def cmd_ssid(app: App, update: Update, ctx):
    uid = update.effective_user.id
    chat = update.effective_chat
    msg = update.effective_message
    m = SSID_RE.match((msg.text or "").strip())
    try:
        await msg.delete()                               # ne laisse jamais le SSID dans la conversation
    except TelegramError:
        pass
    if not m:
        await chat.send_message('Usage : /ssid demo 42["auth",{...}]   (ou /ssid real ...)')
        return
    ssid, is_demo, err = parse_ssid(m.group(2))
    if err:
        await chat.send_message(f"⚠️ SSID refusé : {err}.\nCopiez-le tel quel depuis get_ssid.py, en un seul message.")
        return
    mode = (m.group(1) or ("demo" if is_demo else "real")).lower()
    if (mode == "demo") != is_demo:
        await chat.send_message(f"⚠️ Ce SSID est un SSID {'DÉMO' if is_demo else 'RÉEL'} (isDemo={int(is_demo)}), "
                                f"vous essayez de l'enregistrer en {mode.upper()}. Rien n'a été enregistré.")
        return
    app.store.set_ssid(uid, mode, ssid)
    rt = app.rt(uid)
    if rt.engine and rt.engine.running:
        await chat.send_message(f"✅ SSID {mode.upper()} enregistré (message supprimé). Il servira à la prochaine session.")
        return
    await app.drop_broker(rt)
    if app.settings(uid).mode != mode:
        await chat.send_message(f"✅ SSID {mode.upper()} enregistré (message supprimé). Il sera utilisé au passage en {mode.upper()}.")
        return
    await chat.send_message(f"✅ SSID {mode.upper()} enregistré (message supprimé). Test de connexion…")
    try:
        b = await app.ensure_broker(rt)
        bal = await b.balance()
        await chat.send_message(f"🟢 Connecté à Pocket Option · {MODE_LABEL[mode]} · solde {views.money(bal)}")
    except Exception as e:
        await chat.send_message(f"❌ Connexion impossible : {E(str(e) or repr(e))}\n"
                                "Causes fréquentes : SSID expiré, ou compte ouvert dans le navigateur avec la même session. "
                                "Régénérez-le avec get_ssid.py et renvoyez-le.")


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"Votre ID Telegram : {update.effective_user.id}")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    app: App = ctx.application.bot_data["app"]
    uid = update.effective_user.id
    if uid not in app.cfg.allowed_user_ids:
        return
    rt = app.rt(uid)
    rt.chat_id = update.effective_chat.id
    text = (update.effective_message.text or "").strip()
    if text in KEY_ROUTES:                                  # bouton du clavier permanent
        ctx.user_data.pop("await", None)
        try:
            await route(app, update, ctx, rt, list(KEY_ROUTES[text]))
        except Exception as e:
            log.exception("clavier %s", text)
            await update.effective_message.reply_text(f"⚠️ {str(e) or 'Erreur'}")
        return
    aw = ctx.user_data.get("await")
    if not aw:
        return
    ctx.user_data.pop("await", None)
    s = app.settings(uid)
    try:
        if aw[0] == "stake":
            apply_value(app, uid, s, "stake", text)
            await screen_asset_scope(update, "auto")
        elif aw[0] == "tp":
            apply_value(app, uid, s, "take_profit", text)
            await screen_sl(update, app.settings(uid).take_profit)
        elif aw[0] == "sl":
            apply_value(app, uid, s, "stop_loss", text)
            await launch_and_report(app, update, rt, True)
        else:
            apply_value(app, uid, s, aw[1], text)
            gid = next(g for g, (_, ks) in GROUPS.items() if aw[1] in ks)
            await show(update, f"<b>{GROUPS[gid][0]}</b>", kb_group(app.settings(uid), gid))
    except ValueError as e:
        ctx.user_data["await"] = aw
        await update.effective_message.reply_text(f"⚠️ {e or 'Valeur invalide'}. Réessayez :")


def build_application(cfg: Config, store: Store) -> Application:
    app = App(cfg, store)

    async def post_init(a: Application):
        app.bot = a.bot

    async def post_shutdown(a: Application):
        await app.shutdown()

    tg = Application.builder().token(cfg.telegram_token).post_init(post_init).post_shutdown(post_shutdown).build()
    tg.bot_data["app"] = app
    tg.add_handler(CommandHandler(["start", "menu"], cmd_start))
    tg.add_handler(CommandHandler("stop", cmd_stop))
    tg.add_handler(CommandHandler("ssid", cmd_ssid))
    tg.add_handler(CommandHandler("id", cmd_id))
    tg.add_handler(CallbackQueryHandler(on_callback))
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return tg
