"""Bot Telegram (python-telegram-bot v21+) : menus à boutons, trades en direct, jetons, administration."""
import asyncio
import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from telegram import InlineKeyboardMarkup as M, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest, RetryAfter, TelegramError
import telegram.error as _tgerr

Forbidden = getattr(_tgerr, "Forbidden", TelegramError)      # absent des anciennes versions / du banc d'essai
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters)

import po_web
import views
import visuals
from bot import accounts as AC
from bot import onboarding as ONB
from bot.ui import B, E, MODE_LABEL, PANELS, U, ask, drop, drop_panel, show
from broker import FeedBroker, make_broker
from config import Config
from engine import MG_FACTORS, Engine
from store import PAYOUT_FLOOR, Store, UserSettings
import ssid as ssidlib
from ssid import parse_ssid, po_uid
from strategies import REGISTRY

log = logging.getLogger("bot")

CATS = [("currency", "💱 Devises"), ("other", "📈 Actions, indices, matières"), ("crypto", "₿ Crypto")]

# Clavier permanent (sous la zone de saisie) : accès direct, dont « Arrêter »
REPLY_KB = ReplyKeyboardMarkup(
    [["🚀 Auto-trading", "📡 Signaux"], ["🎮 Manuel", "⏹ Arrêter"], ["📊 Compte", "⚙️ Paramètres"]],
    resize_keyboard=True, is_persistent=True, input_field_placeholder="Menu rapide")
KEY_ROUTES = {"🚀 Auto-trading": ["auto"], "📡 Signaux": ["sig"], "🎮 Manuel": ["man"],
              "⏹ Arrêter": ["run", "stop"], "📊 Compte": ["acc"], "⚙️ Paramètres": ["set"]}

# clé: (libellé, type, presets, min, max)
SPEC = {
    "stake": ("Mise ($)", "float", [1, 2, 5, 10, 25], 1, 5000),
    "speed": ("⚡ Vitesse d'entrée", "choice", ["ultra", "rapide", "normal", "cloture"], 0, 0),
    "market": ("Marché", "choice", ["otc", "real"], 0, 0),
    "min_payout": ("Paiement min (%)", "int", [85, 90, 92], 0, 100),
    "scan_assets": ("Actifs surveillés (auto)", "int", [1, 2, 3, 5, 8], 1, 10),
    "mg_enabled": ("♻️ Martingale ×2,1 → ×2,5", "bool", None, 0, 0),
    "mg_other_pair": ("🔀 Martingale sur une autre paire", "bool", None, 0, 0),
    "series_deals": ("Deals gagnants par série", "int", [5, 7, 10], 1, 100),
    "take_profit": ("Take-profit ($, mode TP, 0 = off)", "float", [5, 10, 20, 50, 100], 0, 1e6),
    "stop_loss": ("Stop-loss ($, mode TP, 0 = off)", "float", [10, 20, 30, 50, 100], 0, 1e6),
    "max_open": ("Trades simultanés max", "int", [1, 2, 3, 4, 6], 1, 20),
}
GROUPS = {
    "sig": ("🧠 Stratégies & vitesse", ["strategies", "speed"]),
    "ast": ("🎯 Actifs (92 % et plus uniquement)", ["market", "scan_assets"]),
    "stk": ("💵 Mise & martingale", ["stake", "mg_enabled", "mg_other_pair"]),
    "rsk": ("🛡 Risque", ["series_deals", "take_profit", "stop_loss", "max_open"]),
}
SPEED_LABEL = {"ultra": "⚡⚡ Ultra", "rapide": "⚡ Rapide", "normal": "Normale", "cloture": "🕯 À la clôture"}
SPEED_DESC = {
    "ultra": "Évalue les stratégies à chaque tick (jusqu'à ~50 fois/s) et entre à la milliseconde où les conditions sont réunies. "
             "Plus de CPU ; un signal peut disparaître avant la fin de la bougie.",
    "rapide": "Évalue ~8 fois/s. Entrée quasi immédiate, charge modérée.",
    "normal": "Évalue ~2 fois/s. Économe, entrée moins précise.",
    "cloture": "N'évalue qu'à la clôture de chaque bougie : signaux stables (pas de repeinture), entrée retardée.",
}
VALUE_FMT = {"speed": SPEED_LABEL, "market": {"otc": "OTC", "real": "Réel"}}


MN = {"demo": "DÉMO", "real": "RÉEL"}          # libellés des modes (avec accents)


class NeedTokens(Exception):
    """Plus assez de jetons pour lancer une session d'auto-trading."""


class NeedSSID(Exception):
    """Pas de SSID personnel : pas d'ordres (auto-trading / manuel). Les signaux restent disponibles."""

    def __init__(self, mode: str):
        super().__init__(f"Aucun SSID {MN[mode]} enregistré : ouvrez Paramètres › Mes comptes pour l'envoyer.")
        self.mode = mode


@dataclass
class TradeMsg:
    """Un message de trade/signal suivi jusqu'à sa suppression (supprimé juste avant le prochain envoi)."""
    d: dict
    deal: object = None
    sid: Optional[int] = None
    msg: object = None
    pic: object = None               # image aléatoire (IMG1) envoyée sous le signal, supprimée avec lui
    state: str = "signal"            # signal (sans ordre) | running | closing (résultat en cours d'affichage) | done
    deleted: bool = False
    task: Optional[asyncio.Task] = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class Runtime:
    def __init__(self, uid: int):
        self.uid = uid
        self.chat_id: Optional[int] = None
        self.broker = None
        self.broker_mode = ""
        self.engine: Optional[Engine] = None
        self.manual: Optional[Engine] = None
        self.dash: Optional[asyncio.Task] = None
        self.dash_msg = None
        self.assets_cache = (0.0, [])
        self.lock = asyncio.Lock()               # espace les appels à l'API Telegram (anti-flood)
        self.tms: List[TradeMsg] = []
        self.by_deal: Dict[int, TradeMsg] = {}
        self.by_sid: Dict[int, TradeMsg] = {}
        self.junk: list = []                     # messages d'info éphémères
        self.old_dash = None                     # tableau de bord de la session précédente (supprimé au prochain lancement)
        self.ssid_lock = asyncio.Lock()
        self.ssid_at = 0.0
        self.bg: list = []                       # tâches différées (suppression des mots de passe affichés…)


class App:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.rts: Dict[int, Runtime] = {}
        self.bot = None
        self.bot_username = cfg.bot_username
        self._feed = None                        # flux de marché partagé (lecture seule) pour les comptes sans SSID
        self._feed_lock = asyncio.Lock()
        self.web = po_web.WebAutomation(cfg, cfg.data_dir)               # création de compte / connexion (Playwright)
        self.partners = po_web.PartnerChecker(cfg, self.web)             # vérification d'ID côté pocketpartners
        self._rl: Dict[str, List[float]] = {}
        self._kb_at: Dict[int, float] = {}                               # chat -> dernière pose du clavier rapide
        self.share: Dict[int, tuple] = {}                                # admin -> (image, légende) du dernier « total » généré
        self._locks: Dict[int, asyncio.Lock] = {}
        self.started_at = time.time()
        self.last_error_notice = 0.0

    # ---------------------------------------------------------------- accès
    def is_admin(self, uid: int) -> bool:
        return uid in self.cfg.admin_ids

    def is_allowed(self, uid: int) -> bool:
        """Accès ouvert : toute personne qui démarre le bot est autorisée, sauf si un admin l'a bloquée."""
        return self.is_admin(uid) or not self.store.is_blocked(uid)

    def touch(self, update: Update) -> None:
        """Enregistre / met à jour le profil (nom, @username) des comptes non admins."""
        u = update.effective_user
        if u is not None and not self.is_admin(u.id):
            self.store.ensure_account(u.id, getattr(u, "full_name", "") or "", getattr(u, "username", "") or "",
                                      welcome=self.cfg.welcome_tokens)

    def rt(self, uid: int) -> Runtime:
        return self.rts.setdefault(uid, Runtime(uid))

    def settings(self, uid: int) -> UserSettings:
        return self.store.settings(uid)

    def session_cost(self, uid: int, s: UserSettings) -> int:
        if self.is_admin(uid):
            return 0
        return self.cfg.cost_auto_real if s.mode == "real" else self.cfg.cost_auto_demo

    # ---------------------------------------------------------------- utilitaires
    def user_lock(self, uid: int) -> asyncio.Lock:
        """Les actions d'un MÊME utilisateur restent ordonnées ; celles d'utilisateurs différents tournent en parallèle."""
        return self._locks.setdefault(uid, asyncio.Lock())

    def rate_ok(self, key: str, limit: int, window: float) -> bool:
        """True si moins de `limit` événements `key` dans les `window` dernières secondes."""
        now = time.time()
        hits = [t for t in self._rl.get(key, []) if now - t < window]
        self._rl[key] = hits
        return len(hits) < limit

    def rate_hit(self, key: str) -> None:
        self._rl.setdefault(key, []).append(time.time())

    def later(self, rt: Runtime, delay: float, fn, *args) -> None:
        """Exécute fn(*args) dans `delay` secondes (annulé proprement à l'arrêt du bot)."""
        async def go():
            try:
                await asyncio.sleep(delay)
                await fn(*args)
            except asyncio.CancelledError:
                return
        t = asyncio.create_task(go())
        rt.bg = [x for x in rt.bg if not x.done()] + [t]

    async def notify_admins(self, text: str, rows=None, photo: str = "") -> None:
        for adm in self.cfg.admin_ids:
            try:
                await self.bot.send_message(adm, text, parse_mode="HTML", reply_markup=M(rows) if rows else None)
                if photo and hasattr(self.bot, "send_photo"):
                    with open(photo, "rb") as f:
                        await self.bot.send_photo(adm, f, caption="Capture de diagnostic")
            except (TelegramError, OSError):
                pass

    async def install_keyboard(self, who) -> None:
        """Installe le clavier permanent (le message qui le porte est supprimé aussitôt, le clavier reste)."""
        chat = getattr(who, "chat_id", None) or who
        try:
            m = await self.bot.send_message(chat, "⌨️", reply_markup=REPLY_KB)
            await drop(m)
        except TelegramError:
            pass

    async def store_ssid(self, rt: Runtime, mode: str, ssid: str, check: bool = True):
        """Enregistre un SSID déjà validé (+ état), invalide l'ancien client et le vérifie. Retourne (ok, détail)."""
        uid = rt.uid
        self.store.set_ssid(uid, mode, ssid)
        self.store.set_ssid_meta(uid, mode, saved=time.time(), verified=0, error="", po_uid=po_uid(ssid))
        if rt.engine and rt.engine.running and rt.broker_mode == mode:
            return None, "il servira à la prochaine session"
        if rt.broker_mode == mode:
            await self.drop_broker(rt)
        if not check:
            return None, "enregistré"
        ok, info = await verify_ssid(self, mode, ssid)
        self.store.set_ssid_meta(uid, mode, verified=time.time() if ok else 0, error=(info if ok is False else ""))
        return ok, info

    # ---------------------------------------------------------------- courtier
    def trading_ssid(self, uid: int, s: UserSettings) -> str:
        """SSID servant à passer des ordres : celui de l'utilisateur (jamais celui d'un autre).
        Seuls les admins peuvent retomber sur les SSID du .env ; BROKER=paper n'en exige pas."""
        own = self.store.get_ssid(uid, s.mode)
        if not own and self.is_admin(uid):
            own = self.cfg.ssid_demo if s.mode == "demo" else self.cfg.ssid_real
        return own or ("paper" if self.cfg.broker == "paper" else "")

    def feed_ssid(self) -> str:
        if self.cfg.ssid_demo:
            return self.cfg.ssid_demo
        for adm in self.cfg.admin_ids:
            ssid = self.store.get_ssid(adm, "demo")
            if ssid:
                return ssid
        return ""

    async def feed_broker(self):
        """Un seul client réseau partagé, en lecture seule, pour les signaux des comptes sans SSID."""
        async with self._feed_lock:
            if self._feed is not None and self._feed.is_connected():
                return self._feed
            ssid = self.feed_ssid()
            if not ssid and self.cfg.broker != "paper":
                raise RuntimeError("Signaux indisponibles pour le moment : enregistrez votre SSID DÉMO "
                                   "dans Paramètres › Mes comptes pour recevoir les signaux.")
            if self._feed is not None:
                try:
                    await self._feed.stop()
                except Exception:
                    pass
            b = make_broker(self.cfg.broker, ssid)
            await asyncio.wait_for(b.start(), 90)
            self._feed = b
            return b

    async def ensure_broker(self, rt: Runtime, trade: bool = True):
        """trade=True : compte PERSONNEL de l'utilisateur (NeedSSID s'il n'a pas de SSID).
        trade=False : signaux seulement ; sans SSID on utilise le flux partagé en lecture seule."""
        s = self.settings(rt.uid)
        ssid = self.trading_ssid(rt.uid, s)
        locked = s.mode == "real" and not self.cfg.allow_real
        if trade and locked:
            raise RuntimeError("Mode RÉEL verrouillé : mettez ALLOW_REAL=1 dans .env pour l'activer.")
        if not ssid or (locked and not trade):
            if trade:
                raise NeedSSID(s.mode)
            if rt.broker is not None and rt.broker_mode == "feed" and rt.broker.is_connected():
                return rt.broker
            await self.drop_broker(rt)
            rt.broker, rt.broker_mode = FeedBroker(await self.feed_broker(), self._feed_lock), "feed"
            return rt.broker
        if rt.broker is not None and rt.broker_mode == s.mode:
            if rt.broker.is_connected():
                return rt.broker
            try:
                await asyncio.wait_for(rt.broker.reconnect(), 90)
                return rt.broker
            except Exception as e:
                log.warning("reconnexion impossible (%s) -> recréation du client", e)
        await self.drop_broker(rt)
        b = make_broker(self.cfg.broker, ssid)
        await asyncio.wait_for(b.start(), 90)
        rt.broker, rt.broker_mode = b, s.mode
        return b

    async def drop_broker(self, rt: Runtime):
        if rt.manual is not None:
            await rt.manual.shutdown()
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
        minp = max(PAYOUT_FLOOR, self.settings(rt.uid).min_payout)
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

    # ---------------------------------------------------------------- envoi (espacé, sans jamais planter)
    async def _call2(self, rt: Runtime, fn, *a, **k):
        """Appel Telegram espacé ; retourne (résultat, statut) avec statut "ok" | "gone" (message supprimé) | "fail"."""
        async with rt.lock:
            for _ in range(3):
                try:
                    r = await fn(*a, **k)
                    await asyncio.sleep(0.1)
                    return r, "ok"
                except RetryAfter as e:
                    ra = e.retry_after
                    await asyncio.sleep(float(ra.total_seconds() if hasattr(ra, 'total_seconds') else ra) + 0.5)
                except BadRequest as e:
                    m = str(e).lower()
                    if "not modified" in m:
                        return None, "ok"
                    if "not found" in m or "can't be edited" in m or "message to" in m:
                        return None, "gone"                  # le message n'existe plus : on cesse de le modifier
                    log.warning("telegram: %s", e)
                    return None, "fail"
                except Forbidden:                                  # l'utilisateur a bloqué le bot : plus personne ne surveille
                    if rt.engine and rt.engine.running and not rt.engine.stopping:
                        log.warning("utilisateur %s injoignable : arrêt de sa session", rt.uid)
                        rt.engine.stop("⛔ Bot bloqué par l'utilisateur")
                    return None, "gone"
                except TelegramError as e:
                    log.warning("telegram: %s", e)
                    return None, "fail"
        return None, "fail"

    async def _call(self, rt: Runtime, fn, *a, **k):
        return (await self._call2(rt, fn, *a, **k))[0]

    async def send(self, rt: Runtime, text: str, rows=None):
        return await self._call(rt, self.bot.send_message, rt.chat_id, text, parse_mode="HTML",
                                reply_markup=M(rows) if rows else None)

    async def send_photo(self, rt: Runtime, photo, caption: str = "", rows=None):
        """Envoie une image : Path du disque (file_id mémorisé ensuite) ou octets générés. Retourne le message ou None."""
        if photo is None or not hasattr(self.bot, "send_photo"):
            return None
        kw = dict(caption=caption or None, parse_mode="HTML" if caption else None, reply_markup=M(rows) if rows else None)
        key = str(photo) if not isinstance(photo, (bytes, bytearray)) else ""
        if key and visuals.FILE_IDS.get(key):
            m = await self._call(rt, self.bot.send_photo, rt.chat_id, visuals.FILE_IDS[key], **kw)
            if m is not None:
                return m
            visuals.FILE_IDS.pop(key, None)                     # file_id périmé : on renvoie le fichier
        try:
            data = photo if not key else open(photo, "rb").read()
        except OSError:
            return None
        m = await self._call(rt, self.bot.send_photo, rt.chat_id, data, **kw)
        if key and m is not None and getattr(m, "photo", None):
            visuals.FILE_IDS[key] = m.photo[-1].file_id
        return m

    async def edit(self, rt: Runtime, msg, text: str, rows=None) -> str:
        """Modifie un message ; retourne "ok" | "gone" (supprimé : ne plus y toucher) | "fail"."""
        if msg is None:
            return "gone"
        return (await self._call2(rt, msg.edit_text, text, parse_mode="HTML",
                                  reply_markup=M(rows) if rows else None))[1]

    async def delete(self, rt: Runtime, msg):
        if msg is not None:
            await self._call(rt, msg.delete)

    async def clean(self, rt: Runtime, keep: Optional[TradeMsg] = None, everything: bool = False) -> None:
        """Supprime les messages de trades terminés / signaux périmés / infos (avant d'en envoyer un nouveau)."""
        gone = [t for t in rt.tms if t is not keep and (everything or t.state in ("signal", "done"))]
        for t in gone:
            t.deleted = True
            rt.tms.remove(t)
            if t.task:
                t.task.cancel()
            if t.deal is not None:
                rt.by_deal.pop(t.deal.id, None)
            if t.sid is not None:
                rt.by_sid.pop(t.sid, None)
            try:
                await asyncio.wait_for(t.ready.wait(), 3)
            except asyncio.TimeoutError:
                pass
            await self.delete(rt, t.msg)
            await self.delete(rt, t.pic)
        junk, rt.junk = rt.junk, []
        for m in junk:
            await self.delete(rt, m)

    # ---------------------------------------------------------------- événements du moteur
    def make_notify(self, rt: Runtime):
        async def notify(kind, engine, **d):
            if not rt.chat_id:
                return
            if kind == "signal":
                await self._on_signal(rt, engine, d)
            elif kind == "position_open":
                await self._on_open(rt, d)
            elif kind == "position_closed":
                await self._on_closed(rt, engine, d)
            elif kind == "error":
                await self._on_error(rt, engine, d)
            elif kind == "info":
                m = await self.send(rt, d["text"])
                if m is not None:
                    rt.junk.append(m)
            elif kind == "session_end":
                await self._on_end(rt, engine)
        return notify

    def _find(self, rt: Runtime, deal) -> Optional[TradeMsg]:
        return rt.by_deal.get(deal.id) or (rt.by_sid.get(deal.ref) if deal.ref is not None else None)

    async def _on_signal(self, rt: Runtime, engine: Engine, d: dict) -> None:
        deal = d.get("deal")
        tm = TradeMsg(d=d, deal=deal, sid=d.get("sid"), state="running" if deal is not None else "signal")
        rt.tms.append(tm)                                     # enregistré avant tout await : position_open le retrouve
        if deal is not None:
            rt.by_deal[deal.id] = tm
        if tm.sid is not None:
            rt.by_sid[tm.sid] = tm
        await self.clean(rt, keep=tm)                         # le signal précédent est supprimé juste avant le nouveau
        rows = None
        if deal is not None:
            stage = "wait"
        elif tm.sid is not None:
            stage = "ready"
            stake = self.settings(rt.uid).stake
            rows = [[B(f"📥 Placer le trade ({views.money(stake)})", f"man:go:{tm.sid}:{stake:g}")], [B("⬅️ Menu", "menu")]]
        else:
            stage = "signal"
        tm.msg = await self.send(rt, views.trade_text(d, stage, deal), rows)
        if self.cfg.signal_images and tm.msg is not None:
            try:
                tm.pic = await self.send_photo(rt, visuals.pick("IMG1"))      # image aléatoire sous le signal
            except Exception:
                log.exception("image IMG1")
        tm.ready.set()

    async def _on_open(self, rt: Runtime, d: dict) -> None:
        deal, pos = d["deal"], d["pos"]
        tm = self._find(rt, deal)
        if tm is None:
            return
        tm.deal, tm.state = deal, "running"
        rt.by_deal[deal.id] = tm
        tm.task = asyncio.create_task(self._animate(rt, tm, deal, pos))

    async def _animate(self, rt: Runtime, tm: TradeMsg, deal, pos) -> None:
        """Animation du résultat : horloge + barre de progression jusqu'à l'expiration."""
        try:
            try:
                await asyncio.wait_for(tm.ready.wait(), 5)
            except asyncio.TimeoutError:
                pass
            if tm.msg is None or tm.deleted:
                return
            total = float(pos.expiration)
            end = pos.opened_at + total
            step = max(1.0, min(5.0, total / 12.0))
            frame = 0
            while True:
                rem = end - time.time()
                if rem <= 0:
                    break
                if tm.deleted or await self.edit(rt, tm.msg, views.trade_text(tm.d, "run", deal, rem=rem, total=total,
                                                                                  frame=frame)) == "gone":
                    return
                frame += 1
                await asyncio.sleep(min(step, max(0.2, rem)))
            await self.edit(rt, tm.msg, views.trade_text(tm.d, "check", deal))
        except asyncio.CancelledError:
            return

    async def _finish_msg(self, rt: Runtime, tm: TradeMsg, stage: str, engine: Engine) -> None:
        tm.state = "closing"                      # protège le message : il ne peut pas être supprimé pendant cet affichage
        if tm.task:
            tm.task.cancel()
            await asyncio.gather(tm.task, return_exceptions=True)
        try:
            await asyncio.wait_for(tm.ready.wait(), 5)
        except asyncio.TimeoutError:
            pass
        rows = [[B("🎮 Autre actif", "man"), B("⬅️ Menu", "menu")]] if engine.kind == "manual" else None
        if not tm.deleted:
            await self.edit(rt, tm.msg, views.trade_text(tm.d, stage, tm.deal), rows)
        tm.state = "done"

    async def _on_closed(self, rt: Runtime, engine: Engine, d: dict) -> None:
        deal, pos = d["deal"], d["pos"]
        tm = self._find(rt, deal)
        if tm is not None:
            tm.deal = deal
            await self._finish_msg(rt, tm, {"win": "win", "loss": "loss"}.get(pos.status, "draw"), engine)

    async def _on_error(self, rt: Runtime, engine: Engine, d: dict) -> None:
        deal = d.get("deal")
        tm = self._find(rt, deal) if deal is not None else None
        if tm is not None:
            tm.deal = deal
            await self._finish_msg(rt, tm, "error", engine)
        else:
            m = await self.send(rt, "⚠️ " + d["text"])
            if m is not None:
                rt.junk.append(m)

    async def _on_end(self, rt: Runtime, engine: Engine) -> None:
        if engine.kind in ("auto", "signals"):
            self.store.mark_session(rt.uid, None)
        await self.clean(rt, everything=True)
        if rt.dash:
            rt.dash.cancel()
            await asyncio.gather(rt.dash, return_exceptions=True)
        if rt.dash_msg is not None and engine.kind in ("auto", "signals"):
            # le déroulé complet reste affiché (état final « 🏁 Terminé »), le résultat arrive juste après
            await self.edit(rt, rt.dash_msg, views.dashboard(engine, final=True), None)
            rt.old_dash = rt.dash_msg
        rt.dash, rt.dash_msg = None, None
        if engine.kind == "auto":
            text = views.session_summary(engine)
            rows = []
            if engine.cfg.mode == "demo":
                rows.append([B("🟢 Je veux ça en réel", "real")])
            rows.append([B("▶️ Relancer", "auto:again"), B("⬅️ Menu", "menu")])
        elif engine.kind == "signals":
            text = f"📡 Scan arrêté · {engine.signals_seen} signaux détectés."
            rows = [[B("▶️ Relancer", "sig:again"), B("⬅️ Menu", "menu")]]
        else:
            text, rows = "🎮 Scan manuel arrêté.", [[B("🎮 Manuel", "man"), B("⬅️ Menu", "menu")]]
        msg = None
        card = await self.result_card(rt, engine) if engine.kind == "auto" else None
        if card is not None:                                  # carte d'image + résultat envoyés ensemble
            if len(text) <= 1024:
                msg = await self.send_photo(rt, card, text, rows)
            else:
                await self.send_photo(rt, card)
        if msg is None:
            msg = await self.send(rt, text, rows)
        if msg is not None:
            PANELS[rt.chat_id] = msg
        await self.ensure_keyboard(rt)

    async def result_card(self, rt: Runtime, engine: Engine) -> Optional[bytes]:
        """Carte de résultat d'une session GAGNANTE : données réelles écrites sur un fond vierge aléatoire (assets/IMG2)."""
        if not self.cfg.result_cards or engine.pnl <= 0:
            return None
        try:
            bg = visuals.pick("IMG2")
            if bg is None:
                return None
            rows = [r for r in self.store.trades(rt.uid, since=engine.started_at) if not r.get("manual")]
            if not rows:
                return None
            by_asset: Dict[str, float] = {}
            for r in rows:
                by_asset[r["asset"]] = by_asset.get(r["asset"], 0.0) + r.get("profit", 0.0)
            best = max(by_asset, key=by_asset.get)
            pair = engine.names.get(best, best)
            step = f"{max(r.get('step', 1) for r in rows)}/{len(MG_FACTORS) + 1}"
            label = " · ".join(sorted({r["strategy"] for r in rows}))
            dur = int(time.time() - engine.started_at)
            handle = f"@{self.bot_username}" if self.bot_username else self.cfg.bot_name
            return await asyncio.to_thread(visuals.render_result, bg, handle, pair, engine.pnl, engine.cfg.stake,
                                           f"{dur // 60}m {dur % 60:02d}s", label, step)
        except Exception:
            log.exception("carte de résultat")
            return None

    async def ensure_keyboard(self, rt: Runtime, force: bool = False) -> None:
        """Remet les boutons rapides à la place du clavier de saisie — sauf pendant une saisie, ou avant l'inscription.
        Limité à une fois par minute (le message porteur est supprimé aussitôt)."""
        if not rt.chat_id or ONB.needs(self, rt.uid):
            return
        now = time.time()
        if not force and now - self._kb_at.get(rt.chat_id, 0.0) < 60:
            return
        self._kb_at[rt.chat_id] = now
        await self.install_keyboard(rt)

    # ---------------------------------------------------------------- sessions
    async def launch(self, rt: Runtime, execute: bool) -> str:
        if rt.engine and rt.engine.running:
            return "Une session est déjà en cours."
        s = self.settings(rt.uid)
        cap = self.stake_cap(s)
        cost = 0
        if execute:
            if not self.trading_ssid(rt.uid, s):
                raise NeedSSID(s.mode)                      # sans SSID personnel : signaux uniquement
            if cap is not None and s.stake > cap:
                return f"Mise ${s.stake:g} > plafond RÉEL ${cap:g} (MAX_REAL_STAKE)."
            cost = self.session_cost(rt.uid, s)
            if cost and self.store.tokens(rt.uid) < cost:
                raise NeedTokens()
        await self.stop_manual(rt)
        b = await self.ensure_broker(rt, trade=execute)
        if execute:
            bal = await b.balance()
            if bal < s.stake:
                return (f"Solde {MN[s.mode]} insuffisant : {views.money(bal)} disponibles pour une mise de "
                        f"{views.money(s.stake)}. Réduisez la mise ou rechargez le compte.")
        await self.clean(rt, everything=True)
        await self.delete(rt, rt.old_dash)
        rt.old_dash = None
        eng = Engine(rt.uid, s, b, self.store, self.make_notify(rt), stake_cap=cap)
        await eng.start(execute)
        if cost:
            self.store.spend_tokens(rt.uid, cost)
        rt.engine = eng
        self.store.mark_session(rt.uid, "auto" if execute else "signals", rt.chat_id)
        msg = await self.send(rt, views.dashboard(eng), [[B("⏹ Arrêter", "run:stop")]])
        rt.dash_msg = msg
        if msg is not None:
            rt.dash = asyncio.create_task(self.dash_loop(rt, eng, msg))
        return ""

    async def dash_loop(self, rt: Runtime, eng: Engine, msg) -> None:
        """Déroulé de la session : rafraîchi à chaque événement ; pendant un trade, le décompte s'anime (~1 s)."""
        last = None
        rows = [[B("⏹ Arrêter", "run:stop")]]
        while eng.running:
            try:
                await asyncio.wait_for(eng.changed.wait(), timeout=1.0 if eng.open_deals else 30)
            except asyncio.TimeoutError:
                pass
            eng.changed.clear()
            if not eng.running:
                return
            text = views.dashboard(eng)
            if text != last:
                if await self.edit(rt, msg, text, rows) == "gone":     # message supprimé : on en recrée un
                    msg = await self.send(rt, text, rows)
                    rt.dash_msg = msg
                    if msg is None:
                        return
                last = text
            await asyncio.sleep(0.5)

    async def start_manual(self, rt: Runtime, symbol: str) -> None:
        """Manuel : scan d'un seul actif ; chaque signal s'affiche avec le bouton « Placer le trade »."""
        await self.stop_manual(rt)
        s = self.settings(rt.uid)
        b = await self.ensure_broker(rt, trade=True)          # « Placer le trade » passe un ordre : SSID personnel requis
        cfg = copy.deepcopy(s)
        cfg.asset_mode, cfg.asset = "manual", symbol
        eng = Engine(rt.uid, cfg, b, self.store, self.make_notify(rt), stake_cap=self.stake_cap(s))
        await eng.start(False, kind="manual")
        rt.manual = eng

    async def stop_manual(self, rt: Runtime) -> None:
        if rt.manual is not None:
            eng, rt.manual = rt.manual, None
            if not eng.open_deals:
                await eng.shutdown()
            else:                                   # un trade manuel est en cours : on le laisse aller à son terme
                eng.stop("Scan manuel arrêté")
        await self.clean(rt)                         # supprime les signaux manuels devenus inutiles

    async def shutdown(self):
        for rt in self.rts.values():
            tasks = [t for t in [rt.dash] + [m.task for m in rt.tms] + list(rt.bg) if t is not None]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if rt.engine:
                await rt.engine.shutdown()
            await self.drop_broker(rt)
        if self._feed is not None:
            try:
                await self._feed.stop()
            except Exception:
                pass



# ==================================================================== UI
def kb_main(app: App, uid: int):
    if app.is_admin(uid):
        return [[B("🚀 Auto-trading", "auto")],
                [B("📡 Signaux", "sig"), B("🎮 Manuel", "man")],
                [B("📊 Compte", "acc"), B("⚙️ Paramètres", "set")],
                [B("🛠 Administration", "adm")]]
    s = app.settings(uid)
    c = app.cfg
    rows = [[B("🚀 Lancer l'auto-trading", "auto")],
            [B("🎮 Mode manuel", "man")],
            [B("🔒📈 Passer en RÉEL" if s.mode == "demo" else "🟠 Revenir en DÉMO", "mode")],
            [B("📊 Mon compte", "acc")],
            [B("👥 Amis", "fr"), B(f"🎁 Bonus ({c.bonus_tokens} 💎)", "bonus")],
            [B("💎 Jetons", "tok"), B("⚙️ Paramètres", "set")],
            [B("💰 Dépôt", "dep")]]
    if c.channel_id:
        rows.append([B(AC.channel_label(app, uid).replace("📣", "📢"), "chan")])
    rows.append([B("❓ FAQ", "faq"), AC.support_button(app)])
    return rows


def main_text(app: App, uid: int) -> str:
    s = app.settings(uid)
    rt = app.rt(uid)
    me = f"@{app.bot_username}" if app.bot_username else app.cfg.bot_name
    if app.is_admin(uid):
        txt = f"🏠 <b>Menu</b>\n\n{MODE_LABEL[s.mode]} · mise ${s.stake:g} · stratégies {', '.join(s.strategies)}"
    else:
        txt = f"🤖 <b>{E(me)}</b>\n\n{MODE_LABEL[s.mode].split(' ')[0]} Mode : <b>{'DÉMO' if s.mode == 'demo' else 'RÉEL'}</b>\n" \
              f"💎 Jetons : <b>{app.store.tokens(uid)}</b>"
    if not app.trading_ssid(uid, s):
        txt += "\n🔑 Sans SSID : signaux uniquement (⚙️ Paramètres › Mes comptes pour trader)"
    if rt.engine and rt.engine.running:
        txt += "\n🟢 Session en cours"
    return txt


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
    await show(update, main_text(app, uid), kb_main(app, uid), banner="menu")


async def screen_stake(app: App, update: Update, uid: int):
    s = app.settings(uid)
    rows = [[B(f"${v:g}", f"auto:stake:{v:g}") for v in (1, 2, 5, 10)],
            [B("✏️ Autre montant", "auto:stake:edit")],
            [B("🔒 Passer en RÉEL" if s.mode == "demo" else "🟠 Revenir en DÉMO", "mode")],
            [B("⬅️ Menu", "menu")]]
    await show(update, f"💵 Choisissez votre mise de départ :\n\nLa session tournera sur : <b>{MODE_LABEL[s.mode]}</b>", rows)


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
               [[B(lbl, f"{kind}:ct:{market}:{c}")] for c, lbl in CATS] + [[B("⬅️ Retour", f"{kind}:mk:{market}" if kind == "man" else f"{kind}:as:man")]])


async def screen_top(app: App, update: Update, rt: Runtime, kind: str, market: str, cat: str):
    top = await app.top_assets(rt, market, cat)
    lbl = dict(CATS)[cat]
    if not top:
        await show(update, f"{lbl}\n\nAucun actif à {max(PAYOUT_FLOOR, app.settings(rt.uid).min_payout)}% ou plus pour le moment.",
                   [[B("⬅️ Retour", f"{kind}:mk:{market}")]])
        return
    rows = [[B(t, f"{kind}:pk:{a.symbol}")] for a, t in top]
    rows.append([B("⬅️ Retour", f"{kind}:mk:{market}")])
    await show(update, f"📊 <b>{'OTC' if market == 'otc' else 'Réel'} · {lbl}</b>\nTop 6 par paiement "
                       f"(winrate à côté, le cas échéant) :", rows)


async def screen_auto_mode(app: App, update: Update, uid: int):
    await show(update,
               "🤖 <b>Mode auto-trading</b>\n\n"
               "🎯 <b>Série de deals gagnants</b> — la session s'arrête quand 5, 7 ou 10 deals sont gagnés. "
               "Ni take-profit ni stop-loss.\n\n"
               "📈 <b>Par take-profit</b> — trade jusqu'au profit (TP) ou à la perte (SL) que vous fixez ensuite.\n\n"
               "♻️ Martingale : après une perte, le bot attend la confirmation suivante de la même stratégie, "
               "puis mise ×2,1 · ×2,2 · ×2,3 · ×2,4 · ×2,5 — <b>jamais sur la même paire</b> que celle perdue (avec « Sélection auto »). "
               "Un « deal » = une mise de base + ses étapes de martingale.",
               [[B("🎯 Série de deals gagnants", "auto:md:series")], [B("📈 Par take-profit", "auto:md:tp")],
                [B("⚙️ Réglages", "set"), B("⬅️ Menu", "menu")]])


async def screen_series(update: Update):
    await show(update, "🎯 <b>Série de deals gagnants</b>\n\nCombien de deals gagnés avant l'arrêt de la session ?\n"
                       "<i>Pas de take-profit ni de stop-loss en mode série.</i>",
               [[B(f"{n} deals", f"auto:sr:{n}") for n in (5, 7, 10)], [B("⬅️ Retour", "auto:st:ok")],
                [B("⬅️ Menu", "menu")]])


async def screen_need_ssid(app: App, update: Update, mode: str):
    await show(update, f"🔑 <b>SSID {MN[mode]} requis</b>\n\n"
                       "L'auto-trading et le trade manuel passent des ordres sur <b>votre</b> compte Pocket Option : "
                       f"enregistrez votre SSID {MN[mode]} dans ⚙️ Paramètres › 🔑 Mes comptes.\n\n"
                       "Sans SSID, vous gardez les 📡 <b>Signaux</b> (aucun ordre n'est passé).",
               [[B("🔑 Enregistrer mon SSID", "set:ss")], [B("📡 Signaux", "sig")], [B("⬅️ Menu", "menu")]])


async def screen_strategies(app: App, update: Update, uid: int, kind: str):
    s = app.settings(uid)
    rows = [[B(("✅ " if k in s.strategies else "⬜ ") + REGISTRY[k].label, f"{kind}:st:t:{k}")] for k in REGISTRY]
    rows.append([B("Toutes", f"{kind}:st:all"), B("▶️ Valider", f"{kind}:st:ok")])
    rows.append([B("⬅️ Menu", "menu")])
    await show(update, "🧠 <b>Stratégies</b>\n\nTouchez pour choisir <b>5s</b>, <b>1M</b>, <b>2M</b> et/ou <b>5M</b>, puis validez.", rows)


async def screen_tp(update: Update):
    await show(update, "🎯 <b>Take-profit</b>\n\nGain de session à partir duquel le bot s'arrête (plus aucune nouvelle position) :",
               [[B(f"+${v}", f"auto:tp:{v}") for v in (5, 10, 20, 50)],
                [B("✏️ Autre montant", "auto:tp:edit"), B("🚫 Aucun", "auto:tp:0")], [B("⬅️ Menu", "menu")]])


async def screen_sl(update: Update, tp: float):
    tps = f"+${tp:g}" if tp > 0 else "aucun"
    await show(update, f"🛑 <b>Stop-loss</b>\n\nTake-profit : <b>{tps}</b>\nPerte de session à partir de laquelle le bot s'arrête "
                       "(un palier de martingale qui ferait dépasser cette limite n'est jamais ouvert) :",
               [[B(f"−${v}", f"auto:sl:{v}") for v in (10, 20, 30, 50)],
                [B("✏️ Autre montant", "auto:sl:edit"), B("🚫 Aucun", "auto:sl:0")], [B("⬅️ Menu", "menu")]])


async def screen_manual_panel(app: App, update: Update, rt: Runtime, symbol: str):
    s = app.settings(rt.uid)
    names = {a.symbol: a for a in await app.assets(rt)}
    a = names.get(symbol)
    stk_row = [B(("✔ " if abs(s.stake - v) < 1e-9 else "") + f"${v:g}", f"man:stk:{v:g}") for v in (1, 2, 5, 10)]
    await show(update,
               f"🎮 <b>Manuel</b> · {E(views.asset_label(symbol, a.name if a else ''))}"
               + (f" · paiement {a.payout}%" if a else "") + "\n\n"
               f"🔎 En attente d'un signal ({' · '.join(s.strategies)})…\n"
               "Dès qu'il apparaît, touchez <b>Placer le trade</b>.\n\n"
               f"💵 Mise : <b>${s.stake:g}</b> · {MODE_LABEL[s.mode]} · gratuit",
               [stk_row, [B("⬅️ Menu", "menu")]])


SSID_PROMPT = {
    "demo": "🟠 <b>SSID DÉMO</b>\n\nCollez ici votre SSID <b>démo</b> Pocket Option (format <code>42[\"auth\",{...}]</code>), "
            "en un seul message.\n🔐 Le message est supprimé automatiquement de la conversation.",
    "real": "🟢 <b>SSID RÉEL</b>\n\nCollez ici votre SSID <b>réel</b> Pocket Option (format <code>42[\"auth\",{...}]</code>), "
            "en un seul message.\n🔐 Le message est supprimé automatiquement de la conversation.",
}


def ago(ts: float) -> str:
    d = max(0, int(time.time() - ts))
    return ("à l'instant" if d < 60 else f"il y a {d // 60} min" if d < 3600
            else f"il y a {d // 3600} h" if d < 86400 else f"il y a {d // 86400} j")


async def screen_ssid(app: App, update: Update, uid: int, note: str = ""):
    """Paramètres > Mes comptes : envoyer / remplacer / tester / supprimer son SSID démo et réel."""
    st = app.store

    def state(mode):
        if not st.get_ssid(uid, mode):
            return "❌ absent"
        m = st.ssid_meta(uid, mode)
        who = f" · compte #{m['po_uid']}" if m.get("po_uid") else ""
        if m.get("verified"):
            return f"✅ vérifié{who} · {ago(m['verified'])}"
        if m.get("error"):
            return f"⚠️ non vérifié{who} — {E(str(m['error'])[:90])}"
        return f"✅ enregistré{who} (vérification au premier usage)"
    rows = [[B("🟠 " + ("Remplacer" if st.get_ssid(uid, "demo") else "Envoyer") + " le SSID DÉMO", "set:ss:demo")],
            [B("🟢 " + ("Remplacer" if st.get_ssid(uid, "real") else "Envoyer") + " le SSID RÉEL", "set:ss:real")]]
    saved = [m for m in ("demo", "real") if st.get_ssid(uid, m)]
    if saved:
        rows.append([B(f"🔄 Tester {MN[m]}", f"set:ss:test:{m}") for m in saved])
        rows.append([B(f"🗑 {MN[m]}", f"set:ss:del:{m}") for m in saved])
    rows.append([B("⬅️ Paramètres", "set")])
    lock = "" if app.cfg.allow_real else "\n🔒 Le mode réel est verrouillé par l'administrateur."
    await show(update, (f"{note}\n\n" if note else "") + "🔑 <b>Mes comptes Pocket Option</b>\n\n"
                       f"🟠 Démo : {state('demo')}\n🟢 Réel : {state('real')}{lock}\n\n"
                       "Le SSID relie le bot à VOTRE compte ; il reste privé et ne sert qu'à passer vos ordres. "
                       "Sans SSID : signaux uniquement.", rows)


async def screen_account(app: App, update: Update, rt: Runtime):
    s = app.settings(rt.uid)
    b = await app.ensure_broker(rt)
    bal = await b.balance()
    txt = (f"📊 <b>Mon compte</b>\n\n{MODE_LABEL[s.mode]}\n💰 Solde {'démo' if s.mode == 'demo' else 'réel'} : "
           f"<b>{views.money(bal)}</b>\n🔌 Connexion : {'✅' if b.is_connected() else '❌'}")
    if not app.is_admin(rt.uid):
        txt += f"\n💎 Jetons : <b>{app.store.tokens(rt.uid)}</b>"
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


# ==================================================================== routage
def err_rows(err: str):
    rows = [[B("🔑 Mes comptes (SSID)", "set:ss")]] if "SSID" in err else []
    return rows + [[B("⚙️ Paramètres", "set"), B("⬅️ Menu", "menu")]]


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    app: App = ctx.application.bot_data["app"]
    q = update.callback_query
    uid = update.effective_user.id
    if not app.is_allowed(uid):
        await q.answer("Accès refusé", show_alert=True)
        return
    app.touch(update)
    rt = app.rt(uid)
    rt.chat_id = update.effective_chat.id
    p = q.data.split(":")
    late = p[0] in ("run", "mode") or (p[0] == "man" and len(p) > 2 and p[1] == "go")   # réponse avec message flash
    if not late:
        await q.answer()
    toast = None
    try:
        toast = await route(app, update, ctx, rt, p)
    except Exception as e:
        log.exception("callback %s", q.data)
        await show(update, f"⚠️ {E(str(e)) or 'Erreur'}", err_rows(str(e)))
    if late:
        try:
            await (q.answer(toast) if toast else q.answer())
        except TelegramError:
            pass


async def route(app: App, update: Update, ctx, rt: Runtime, p: List[str]) -> Optional[str]:
    uid, s = rt.uid, app.settings(rt.uid)
    head = p[0]
    ctx.user_data.pop("await", None)
    ctx.user_data.pop("prompt", None)

    if ONB.needs(app, uid) and head not in ("onb", "sup", "noop"):         # ID pas encore vérifié : on revient à l'accueil
        await ONB.screen_welcome(app, update)
        return None
    if head == "menu":
        await app.stop_manual(rt)
        await screen_menu(app, update, uid)
        await app.ensure_keyboard(rt)                     # retour au menu : boutons rapides remis (1 fois / minute max)
    elif head == "noop":
        return None

    # ---- inscription / vérification d'ID / connexion ----------------------------------
    elif head == "onb":
        await route_onboarding(app, update, ctx, rt, p)

    # ---- bonus / dépôt / FAQ -----------------------------------------------------------
    elif head == "bonus":
        await AC.screen_bonus(app, update, uid)
    elif head == "dep":
        await (AC.deposit_check(app, update, uid) if p[1:] == ["check"] else AC.screen_deposit(app, update, uid))
    elif head == "faq":
        await AC.screen_faq(app, update)

    # ---- jetons / canal / amis / support ---------------------------------------------
    elif head == "tok":
        await AC.screen_tokens(app, update, uid)
    elif head == "chan":
        await (AC.channel_verify(app, update, uid) if len(p) > 1 and p[1] == "ok" else AC.screen_channel(app, update, uid))
    elif head == "fr":
        await AC.screen_friends(app, update, uid)
    elif head == "sup":
        await AC.screen_support(app, update)
    elif head == "adm":
        if app.is_admin(uid):
            await route_admin(app, update, ctx, rt, p)
    elif head == "real":
        await route_real(app, update, rt, s)

    # ---- auto-trading -------------------------------------------------------
    elif head == "auto" and len(p) == 1:
        await app.stop_manual(rt)
        await (screen_stake(app, update, uid) if app.trading_ssid(uid, s) else screen_need_ssid(app, update, s.mode))
    elif head == "auto" and p[1] == "again":
        await launch_and_report(app, update, rt, True)
    elif head == "sig" and p[1:] == ["again"]:
        await launch_and_report(app, update, rt, False)
    elif head == "sig" and len(p) == 1:
        await app.stop_manual(rt)
        await screen_asset_scope(update, "sig")
    elif head == "man" and len(p) == 1:
        await app.stop_manual(rt)
        await (screen_market(update, "man") if app.trading_ssid(uid, s) else screen_need_ssid(app, update, s.mode))
    elif head == "auto" and p[1] == "stake":
        if p[2] == "edit":
            await ask(update, ctx, "✏️ Envoyez le montant de la mise (ex. 3.5) :", [[B("⬅️ Annuler", "auto")]], ("stake", "auto"))
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
        await (screen_series(update) if p[2] == "series" else screen_tp(update))      # série : ni TP ni SL
    elif head == "auto" and p[1] == "sr":
        s.session_mode, s.series_deals = "series", int(p[2]); app.store.save_settings(uid, s)
        await launch_and_report(app, update, rt, True)
    elif head == "auto" and p[1] == "tp":
        if p[2] == "edit":
            await ask(update, ctx, "✏️ Envoyez le take-profit en $ (ex. 15) :", [[B("⬅️ Annuler", "menu")]], ("tp", "auto"))
        else:
            s.take_profit = float(p[2]); app.store.save_settings(uid, s)
            await screen_sl(update, s.take_profit)
    elif head == "auto" and p[1] == "sl":
        if p[2] == "edit":
            await ask(update, ctx, "✏️ Envoyez le stop-loss en $ (ex. 25) :", [[B("⬅️ Annuler", "menu")]], ("sl", "auto"))
        else:
            s.stop_loss = float(p[2]); app.store.save_settings(uid, s)
            await launch_and_report(app, update, rt, True)

    # ---- manuel : choix de l'actif -> scan -> signal -> bouton « Placer le trade » ---------------------
    elif head == "man" and p[1] == "pk":
        symbol = ":".join(p[2:])
        try:
            await app.start_manual(rt, symbol)
        except NeedSSID as e:
            await screen_need_ssid(app, update, e.mode)
            return None
        ctx.user_data["man_asset"] = symbol
        await screen_manual_panel(app, update, rt, symbol)
    elif head == "man" and p[1] == "stk":
        s.stake = float(p[2]); app.store.save_settings(uid, s)
        if ctx.user_data.get("man_asset"):
            await screen_manual_panel(app, update, rt, ctx.user_data["man_asset"])
    elif head == "man" and p[1] == "go":
        return await manual_go(app, update, rt, int(p[2]), float(p[3]))

    # ---- arrêt --------------------------------------------------------------------
    elif head == "run" and p[1] == "stop":
        if rt.engine and rt.engine.running:
            rt.engine.stop("Arrêt manuel")
            return "Arrêt demandé : plus aucune nouvelle position."
        if rt.manual and rt.manual.running:
            await app.stop_manual(rt)
            return "Scan manuel arrêté."
        return "Aucune session en cours."

    # ---- compte / stats / mode ----------------------------------------------------
    elif head == "acc" and len(p) == 1:
        await screen_account(app, update, rt)
    elif head == "acc" and p[1] == "stats":
        await screen_stats(app, update, uid, p[2])
    elif head == "mode":
        return await route_mode(app, update, rt, s, p)

    # ---- paramètres ---------------------------------------------------------------
    elif head == "set":
        await route_settings(app, update, ctx, rt, s, p)
    return None


async def route_onboarding(app: App, update: Update, ctx, rt: Runtime, p: List[str]):
    uid = rt.uid
    step = p[1] if len(p) > 1 else ""
    if step == "reg":
        await ONB.screen_register(app, update)
    elif step == "new":
        if not (app.cfg.auto_signup and app.web.available() and app.cfg.po_register_url):
            await ONB.screen_register(app, update)
        elif app.store.account(uid)["consent"]:
            await ONB.ask_email(app, update, ctx)
        else:
            await ONB.screen_consent(app, update)
    elif step == "consent":
        app.store.set_consent(uid)
        await ONB.ask_email(app, update, ctx)
    elif step == "have":
        await ONB.ask_id(app, update, ctx)
    elif step == "ssid":
        await ONB.screen_ssid_help(app, update)
    elif step == "login":
        await ONB.screen_login_help(app, update, ctx)
    elif step == "ok":
        await ONB.screen_connect(app, update)
    else:
        await ONB.screen_welcome(app, update)


async def launch_and_report(app: App, update: Update, rt: Runtime, execute: bool):
    try:
        err = await app.launch(rt, execute)
    except NeedTokens:
        await AC.screen_no_tokens(app, update, rt.uid)
        return
    except NeedSSID as e:
        await screen_need_ssid(app, update, e.mode)
        return
    except Exception as e:
        err = str(e) or repr(e)
    if err:
        await show(update, f"⚠️ {err}", err_rows(err))
        return
    q = update.callback_query
    if q:                                        # l'écran de configuration est remplacé par la barre d'état
        PANELS.pop(update.effective_chat.id, None)
        await drop(q.message)
    else:
        await drop_panel(update.effective_chat.id)


async def manual_go(app: App, update: Update, rt: Runtime, sid: int, stake: float) -> Optional[str]:
    eng = rt.manual
    if eng is None or not eng.running:
        return "Scan manuel arrêté — relancez depuis le menu."
    tm = rt.by_sid.get(sid)
    try:
        deal = await eng.place_signal(sid, stake)
    except ValueError as e:
        return f"⛔ {e}"
    if tm is not None:
        tm.deal, tm.state = deal, "running"
        rt.by_deal[deal.id] = tm
        await app.edit(rt, tm.msg, views.trade_text(tm.d, "wait", deal))
    return "Ordre envoyé ✅"


async def route_real(app: App, update: Update, rt: Runtime, s: UserSettings):
    """« Je veux ça en réel » : bascule sur le compte RÉEL (pas démo) puis propose de relancer."""
    back = [[B("⬅️ Menu", "menu")]]
    if rt.engine and rt.engine.running:
        await show(update, "Arrêtez la session en cours avant de passer en réel.", back)
    elif not app.cfg.allow_real:
        await show(update, "🔒 Le mode RÉEL est verrouillé par l'administrateur (<code>ALLOW_REAL</code>).", back)
    elif not app.trading_ssid(rt.uid, UserSettings(mode="real")):
        await show(update, "🔑 <b>SSID RÉEL requis</b>\n\nEnregistrez votre SSID réel dans ⚙️ Paramètres › 🔑 Mes comptes, "
                           "puis touchez « Je veux ça en réel » à nouveau.",
                   [[B("🔑 Enregistrer mon SSID", "set:ss")], [B("🟢 Je veux ça en réel", "real")]] + back)
    else:
        note = ""
        cap = app.stake_cap(UserSettings(mode="real"))
        if cap is not None and s.stake > cap:
            s.stake, note = cap, f"\n⚠️ Mise ramenée au plafond réel : ${cap:g}."
        s.mode = "real"
        app.store.save_settings(rt.uid, s)
        await show(update, f"🟢 <b>Compte RÉEL activé</b>{note}\n\nLes prochains trades utiliseront de l'argent réel "
                           f"(mise ${s.stake:g}). Mêmes réglages qu'avant.",
                   [[B("▶️ Relancer", "auto:again"), B("⬅️ Menu", "menu")]])


async def route_mode(app: App, update: Update, rt: Runtime, s: UserSettings, p: List[str]) -> Optional[str]:
    if rt.engine and rt.engine.running:
        return "Arrêtez la session avant de changer de mode."
    if s.mode == "real":
        s.mode = "demo"; app.store.save_settings(rt.uid, s)
        await screen_menu(app, update, rt.uid)
        return None
    if not app.cfg.allow_real:
        await show(update, "🔒 Le mode RÉEL est verrouillé.\nMettez <code>ALLOW_REAL=1</code> dans <code>.env</code> "
                           "puis redémarrez le bot.", [[B("⬅️ Menu", "menu")]])
    elif len(p) > 1 and p[1] == "ok":
        if not app.trading_ssid(rt.uid, UserSettings(mode="real")):
            await screen_need_ssid(app, update, "real")
            return None
        s.mode = "real"; app.store.save_settings(rt.uid, s)
        await screen_menu(app, update, rt.uid)
    else:
        await show(update, "⚠️ <b>Passer en RÉEL ?</b>\n\nLes ordres utiliseront de l'argent réel. Plafond de mise : "
                           f"${app.cfg.max_real_stake:g}. La martingale peut multiplier les pertes.",
                   [[B("✅ Je confirme", "mode:ok"), B("❌ Annuler", "menu")]])
    return None


async def route_settings(app: App, update: Update, ctx, rt: Runtime, s: UserSettings, p: List[str]):
    uid = rt.uid
    if len(p) == 1:
        rows = ([[B(lbl, f"set:g:{gid}")] for gid, (lbl, _) in GROUPS.items()]
                + [[B("🔑 Mes comptes (SSID démo / réel)", "set:ss")], [B("⬅️ Menu", "menu")]])
        await show(update, "⚙️ <b>Paramètres</b>", rows, banner="settings")
    elif p[1] == "ss":
        if len(p) == 2:
            await screen_ssid(app, update, uid)
        elif p[2] in ("demo", "real"):                  # set:ss:demo -> demande le SSID
            await ask(update, ctx, SSID_PROMPT[p[2]], [[B("⬅️ Annuler", "set:ss")]], ("ssid", p[2]))
        elif p[2] == "test":
            await show(update, f"⏳ Test de connexion {MN[p[3]]} en cours…", [])
            await screen_ssid(app, update, uid, await test_ssid(app, rt, p[3]))
        elif p[2] == "del":
            app.store.del_ssid(uid, p[3])
            await app.drop_broker(rt)
            await screen_ssid(app, update, uid, f"🗑 SSID {MN[p[3]]} supprimé.")
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
        await ask(update, ctx, f"✏️ Envoyez la nouvelle valeur pour <b>{SPEC[p[2]][0]}</b> :", [[B("⬅️ Annuler", "set")]], ("set", p[2]))


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


# ==================================================================== administration
async def route_admin(app: App, update: Update, ctx, rt: Runtime, p: List[str]):
    uid = rt.uid
    if len(p) == 1:
        await AC.screen_admin(app, update)
    elif p[1] == "u":
        await AC.screen_users(app, update, int(p[2]))
    elif p[1] == "usr":
        await AC.screen_user(app, update, int(p[2]))
    elif p[1] == "tk":                                   # ajout rapide de jetons
        target, n = int(p[2]), int(p[3])
        bal = app.store.add_tokens(target, n)
        await AC.notify_user(app, target, f"🎁 L'administrateur vous a ajouté <b>{n}</b> jetons. 💎 Solde : <b>{bal}</b>")
        await AC.screen_user(app, update, target)
    elif p[1] == "tke":
        await ask(update, ctx, "✏️ Envoyez le nombre de jetons à <b>ajouter</b> (négatif pour en retirer) :",
                  [[B("⬅️ Annuler", f"adm:usr:{p[2]}")]], ("adm_tok", int(p[2])))
    elif p[1] == "acc":
        target = int(p[2])
        app.store.ensure_account(target)
        app.store.set_auth(target, not app.is_allowed(target))
        await AC.screen_user(app, update, target)
    elif p[1] == "add":
        await ask(update, ctx, "➕ <b>Ajouter / créditer un utilisateur</b>\n\nTout le monde peut démarrer le bot ; ici vous créez ou créditez "
                               "un compte par son ID Telegram (/id), avec des jetons : <code>123456789 25</code>",
                  [[B("⬅️ Annuler", "adm")]], ("adm_add",))
    elif p[1] == "pend":
        await AC.screen_pending(app, update)
    elif p[1] == "ver":
        await AC.verification_action(app, update, int(p[3]), p[2] == "ok")
    elif p[1] == "find":
        await ask(update, ctx, "🔎 <b>Rechercher</b>\n\nEnvoyez un ID Telegram, un @pseudo, un nom ou un ID Pocket Option :",
                  [[B("⬅️ Annuler", "adm")]], ("adm_find",))
    elif p[1] in ("diag", "diagp"):
        await show(update, "🩺 Diagnostic en cours…", [])
        await AC.screen_diag(app, update, live_partner=p[1] == "diagp")
    elif p[1] == "dep":
        await AC.credit_deposit(app, update, int(p[2]), int(p[3]))
    elif p[1] == "stats":
        await AC.screen_global_stats(app, update)
    elif p[1] == "sh":
        if len(p) == 2:
            await AC.screen_share(app, update)
        else:
            await AC.share_generate(app, update, p[2])
    elif p[1] == "shpub":
        await AC.share_publish(app, update, p[2])
    elif p[1] == "bc":
        await ask(update, ctx, "📣 <b>Diffusion</b>\n\nEnvoyez le message à transmettre à tous les utilisateurs :",
                  [[B("⬅️ Annuler", "adm")]], ("adm_bc",))
    elif p[1] == "bcok":
        text = ctx.user_data.pop("bc_text", "")
        ids = {u for u in set(app.store.accounts()) | set(app.cfg.allowed_user_ids) if app.is_allowed(u) and u != uid}
        ok = 0
        for u in ids:
            try:
                await app.bot.send_message(u, text)
                ok += 1
            except TelegramError:
                pass
            await asyncio.sleep(0.06)
        await show(update, f"📣 Message envoyé à <b>{ok}</b>/{len(ids)} utilisateurs.", [[B("⬅️ Admin", "adm")]])


# ==================================================================== SSID
SSID_RE = re.compile(r"^/ssid(?:@\w+)?\s+(?:(demo|real)\s+)?(.+)$", re.I | re.S)


async def verify_ssid(app: App, mode: str, ssid: str):
    """Connexion de contrôle avec un client temporaire (fermé aussitôt). Retourne (ok, détail) :
    ok = True (connecté) | False (échec) | None (vérification différée)."""
    if app.cfg.broker == "paper":
        return True, "simulateur"
    if mode == "real" and not app.cfg.allow_real:
        return None, "mode réel verrouillé : vérification à l'activation"
    b = make_broker(app.cfg.broker, ssid)
    try:
        await asyncio.wait_for(b.start(), 45)
        bal = await asyncio.wait_for(b.balance(), 15)
        try:
            demo = bool(b.is_demo())
        except Exception:
            demo = mode == "demo"
        if demo != (mode == "demo"):
            return False, f"ce SSID est celui d'un compte {'DÉMO' if demo else 'RÉEL'}, pas {MN[mode]}"
        return True, views.money(bal)
    except asyncio.TimeoutError:
        return False, "délai dépassé (Pocket Option ne répond pas)"
    except Exception as e:
        return False, str(e) or e.__class__.__name__
    finally:
        try:
            await b.stop()
        except Exception:
            pass


async def save_ssid(app: App, rt: Runtime, mode_hint: Optional[str], raw: str, deleted: bool = True):
    """Valide, enregistre puis VÉRIFIE un SSID (commande /ssid ou Paramètres). Retourne (ok, message HTML).
    Accepte le SSID complet 42["auth",…] OU la simple valeur du cookie ci_session (l'ID du compte est alors connu du bot)."""
    uid = rt.uid
    if rt.ssid_lock.locked() or time.time() - rt.ssid_at < 1.5:
        return False, "⏳ Un enregistrement est déjà en cours, patientez quelques secondes."
    async with rt.ssid_lock:
        if ssidlib.looks_like_cookie(raw):                      # cookie ci_session collé tel quel
            mode = (mode_hint or "").lower()
            if mode not in ("demo", "real"):
                return False, "Pour un cookie, précisez le type : utilisez les boutons Démo / Réel de Paramètres › Mes comptes."
            ssid, err = ssidlib.from_cookie(raw, int(app.store.account(uid)["po_id"] or 0) or None, mode == "demo")
            if err:
                return False, f"Cookie refusé : {err}."
            is_demo = mode == "demo"
        else:
            ssid, is_demo, err = parse_ssid(raw)
            if err:
                return False, f"SSID refusé : {err}.\nCopiez-le tel quel, en un seul message."
            mode = (mode_hint or ("" if is_demo is None else "demo" if is_demo else "real")).lower()
            if mode not in ("demo", "real"):
                return False, "Type de compte indéterminé : utilisez les boutons Démo / Réel de Paramètres › Mes comptes."
        if is_demo is not None and (mode == "demo") != is_demo:
            return False, (f"Ce SSID est un SSID {'DÉMO' if is_demo else 'RÉEL'} : vous essayez de l'enregistrer en "
                           f"{MN[mode]}. Rien n'a été enregistré.")
        warn = "" if deleted else "\n⚠️ Supprimez vous-même votre message contenant le SSID (je n'ai pas pu le faire)."
        head = f"✅ SSID {MN[mode]} enregistré"
        ok, info = await app.store_ssid(rt, mode, ssid)
        rt.ssid_at = time.time()
        if ok:
            msg = f"{head} · 🟢 connecté · solde {E(info)}"
        elif ok is None:
            msg = f"{head}. {E(info)[:1].upper() + E(info)[1:]}."
        else:
            msg = (f"{head}, mais ⚠️ connexion impossible : {E(info)}\n"
                   "SSID expiré ou compte ouvert ailleurs ? Renvoyez un SSID frais ou touchez 🔄 Tester.")
        return True, msg + warn


async def test_ssid(app: App, rt: Runtime, mode: str) -> str:
    """Bouton « Tester » : relit le SSID enregistré et tente une connexion de contrôle."""
    ssid = app.store.get_ssid(rt.uid, mode)
    if not ssid:
        return f"❌ Aucun SSID {MN[mode]} enregistré."
    async with rt.ssid_lock:
        ok, info = await verify_ssid(app, mode, ssid)
    app.store.set_ssid_meta(rt.uid, mode, verified=time.time() if ok else 0, error=(info if ok is False else ""))
    return (f"🟢 SSID {MN[mode]} valide · solde {E(info)}" if ok else
            f"ℹ️ SSID {MN[mode]} : {E(info)}" if ok is None else
            f"⚠️ SSID {MN[mode]} : connexion impossible — {E(info)}\nRenvoyez un SSID frais.")


# ==================================================================== commandes
def guard(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        app: App = ctx.application.bot_data["app"]
        uid = update.effective_user.id
        if not app.is_allowed(uid):
            await update.effective_message.reply_text("⛔ Votre accès a été suspendu par l'administrateur.")
            return
        app.touch(update)
        app.rt(uid).chat_id = update.effective_chat.id
        await fn(app, update, ctx)
    return wrapper


def parse_ref(args) -> Optional[int]:
    for a in args or []:
        m = re.fullmatch(r"ref_(\d+)", a)
        if m:
            return int(m.group(1))
    return None


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    app: App = ctx.application.bot_data["app"]
    u, chat = update.effective_user, update.effective_chat
    uid = u.id
    if not app.is_allowed(uid):                          # accès ouvert à tous : seul un blocage admin refuse
        await update.effective_message.reply_text("⛔ Votre accès a été suspendu par l'administrateur.")
        return
    created = False
    if not app.is_admin(uid):
        created = app.store.ensure_account(uid, getattr(u, "full_name", "") or "", getattr(u, "username", "") or "",
                                           welcome=app.cfg.welcome_tokens)
    ref = parse_ref(ctx.args)
    # parrainage : seul un NOUVEAU compte arrivé par le lien compte ; le parrain reçoit ses jetons
    if (created and ref is not None and ref != uid and (app.is_admin(ref) or app.store.has_account(ref))
            and not app.store.is_blocked(ref)):
        if app.store.register_referral(uid, ref, app.cfg.ref_bonus):
            await AC.notify_user(app, ref, f"🎉 Un ami a rejoint grâce à votre lien : <b>+{app.cfg.ref_bonus} jetons</b> !\n"
                                           f"💎 Solde : <b>{app.store.tokens(ref)}</b>")
    rt = app.rt(uid)
    rt.chat_id = chat.id
    await drop(update.effective_message)
    await drop_panel(chat.id)
    if ONB.needs(app, uid):                                           # inscription / vérification d'abord
        await ONB.screen_welcome(app, update)
        return
    await app.install_keyboard(rt)                                    # clavier permanent (le message porteur est supprimé)
    await screen_menu(app, update, uid)


@guard
async def cmd_stop(app: App, update: Update, ctx):
    rt = app.rt(update.effective_user.id)
    if rt.engine and rt.engine.running:
        rt.engine.stop("Arrêt manuel")
    else:
        await app.stop_manual(rt)
    await drop(update.effective_message)


@guard
async def cmd_ssid(app: App, update: Update, ctx):
    rt = app.rt(update.effective_user.id)
    chat = update.effective_chat
    msg = update.effective_message
    m = SSID_RE.match((msg.text or "").strip())
    deleted = await drop(msg)                            # ne laisse jamais le SSID dans la conversation
    if not m:
        await chat.send_message('Usage : /ssid demo 42["auth",{...}]   (ou /ssid real ...)\n'
                                'Plus simple : ⚙️ Paramètres › 🔑 Mes comptes (SSID).')
        return
    ok, text = await save_ssid(app, rt, (m.group(1) or "").lower() or None, m.group(2), deleted)
    await chat.send_message(text if ok else f"⚠️ {E(text)}", parse_mode="HTML")


async def cmd_version(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from version import __version__
    await update.effective_message.reply_text(f"Version {__version__}")


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"Votre ID Telegram : {update.effective_user.id}")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    app: App = ctx.application.bot_data["app"]
    uid = update.effective_user.id
    if not app.is_allowed(uid):
        return
    app.touch(update)
    rt = app.rt(uid)
    chat = update.effective_chat
    rt.chat_id = chat.id
    msg = update.effective_message
    text = (msg.text or "").strip()
    if text in KEY_ROUTES:                                  # bouton du clavier permanent
        ctx.user_data.pop("await", None)
        await drop(msg)
        await drop_panel(chat.id)
        try:
            toast = await route(app, update, ctx, rt, list(KEY_ROUTES[text]))
            if toast:
                m = await chat.send_message(toast)
                rt.junk.append(m)
        except Exception as e:
            log.exception("clavier %s", text)
            await chat.send_message(f"⚠️ {str(e) or 'Erreur'}")
        return
    aw = ctx.user_data.get("await")
    deleted = await drop(msg)                               # la réponse de l'utilisateur ne reste jamais dans le chat
    if not aw:
        return
    try:
        await _on_text_input(app, update, ctx, rt, uid, aw, text, deleted)
    finally:
        if not ctx.user_data.get("await"):                  # saisie terminée : les boutons reprennent la place du clavier
            await app.ensure_keyboard(rt, force=True)


async def _on_text_input(app: App, update: Update, ctx, rt: Runtime, uid: int, aw: tuple, text: str, deleted: bool):
    prompt = ctx.user_data.get("prompt")
    s = app.settings(uid)
    try:
        if aw[0] in ("adm_add", "adm_tok", "adm_bc", "adm_find") and not app.is_admin(uid):
            return
        if aw[0] == "onb_email":
            await ONB.handle_email(app, update, ctx, rt, text, deleted)
            return
        if aw[0] == "onb_id":
            await ONB.handle_id(app, update, ctx, rt, text)
            return
        if aw[0] == "onb_lemail":
            await ONB.handle_login_email(app, update, ctx, rt, text)
            return
        if aw[0] == "onb_lpw":
            await ONB.handle_login_password(app, update, ctx, rt, text, deleted)
            return
        if aw[0] == "adm_find":
            ids = app.store.find_accounts(text)
            ctx.user_data.pop("await", None)
            await drop(prompt)
            await AC.screen_matches(app, update, ids)
            return
        if aw[0] == "ssid":                                 # SSID envoyé depuis Paramètres > Mes comptes
            ok, note = await save_ssid(app, rt, aw[1], text, deleted)
            if not ok:
                raise ValueError(note)
            ctx.user_data.pop("await", None)
            await drop(prompt)
            await screen_ssid(app, update, uid, note)
            return
        if aw[0] == "adm_add":
            target, tokens = AC.parse_add(text)
            app.store.ensure_account(target)
            app.store.set_auth(target, True)
            if tokens:
                app.store.add_tokens(target, tokens)
            await AC.notify_user(app, target, "✅ Votre compte est prêt. Envoyez /start pour commencer."
                                 + (f"\n🎁 +{tokens} jetons offerts." if tokens else ""))
            ctx.user_data.pop("await", None)
            await drop(prompt)
            await AC.screen_user(app, update, target)
            return
        if aw[0] == "adm_tok":
            try:
                n = int(text.replace("+", ""))
            except ValueError:
                raise ValueError("Envoyez un nombre entier (ex. 25 ou -10)")
            bal = app.store.add_tokens(aw[1], n)
            if n > 0:
                await AC.notify_user(app, aw[1], f"🎁 L'administrateur vous a ajouté <b>{n}</b> jetons. 💎 Solde : <b>{bal}</b>")
            ctx.user_data.pop("await", None)
            await drop(prompt)
            await AC.screen_user(app, update, aw[1])
            return
        if aw[0] == "adm_bc":
            ctx.user_data.pop("await", None)
            ctx.user_data["bc_text"] = text
            await drop(prompt)
            await show(update, f"📣 <b>Aperçu</b>\n\n{E(text)}\n\nEnvoyer à tous les utilisateurs ?",
                       [[B("✅ Envoyer", "adm:bcok"), B("❌ Annuler", "adm")]])
            return
        if aw[0] == "stake":
            apply_value(app, uid, s, "stake", text)
            ctx.user_data.pop("await", None)
            await drop(prompt)
            await screen_asset_scope(update, "auto")
        elif aw[0] == "tp":
            apply_value(app, uid, s, "take_profit", text)
            ctx.user_data.pop("await", None)
            await drop(prompt)
            await screen_sl(update, app.settings(uid).take_profit)
        elif aw[0] == "sl":
            apply_value(app, uid, s, "stop_loss", text)
            ctx.user_data.pop("await", None)
            await drop(prompt)
            await launch_and_report(app, update, rt, True)
        else:
            apply_value(app, uid, s, aw[1], text)
            ctx.user_data.pop("await", None)
            await drop(prompt)
            gid = next(g for g, (_, ks) in GROUPS.items() if aw[1] in ks)
            await show(update, f"<b>{GROUPS[gid][0]}</b>", kb_group(app.settings(uid), gid))
    except ValueError as e:                                  # saisie invalide : on réaffiche le prompt avec l'erreur
        ctx.user_data["await"] = aw
        if prompt is not None:
            try:
                await prompt.edit_text(f"⚠️ {E(str(e)) or 'Valeur invalide'}\n\n" + ctx.user_data.get("prompt_text", ""),
                                       parse_mode="HTML", reply_markup=M(ctx.user_data.get("prompt_rows", [])))
            except TelegramError:
                pass


def serialized(fn):
    """Exécute le gestionnaire sous le verrou de l'utilisateur : ordre garanti pour lui, parallélisme entre utilisateurs
    (une inscription de 60 s ne bloque donc plus personne d'autre)."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        app: App = ctx.application.bot_data["app"]
        u = update.effective_user
        if u is None:
            return await fn(update, ctx)
        async with app.user_lock(u.id):
            return await fn(update, ctx)
    wrapper.__name__ = getattr(fn, "__name__", "handler")
    return wrapper


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    """Aucune exception ne doit faire tomber le bot : on journalise, on prévient l'admin (au plus 1 fois / 10 min)."""
    app: App = ctx.application.bot_data["app"]
    err = ctx.error
    name = err.__class__.__name__
    if isinstance(err, (_tgerr.NetworkError, _tgerr.TimedOut)):
        log.warning("réseau Telegram : %s (nouvelle tentative automatique)", err)
        return
    if name == "Conflict":
        log.error("CONFLIT : une autre instance du bot utilise déjà ce token (getUpdates). Arrêtez l'autre instance "
                  "(ancien terminal, autre serveur, conteneur…).")
        return
    if isinstance(err, RetryAfter):
        log.warning("Telegram demande de ralentir (%s s)", getattr(err, "retry_after", "?"))
        return
    log.error("exception non gérée : %s", err, exc_info=err)
    if time.time() - app.last_error_notice > 600:
        app.last_error_notice = time.time()
        await app.notify_admins(f"⚠️ Erreur interne : <code>{E(name)}</code> — {E(str(err)[:200])}\nConsultez les logs (data/logs/bot.log).")
    try:
        chat = getattr(update, "effective_chat", None)
        if chat is not None and app.rate_ok(f"err:{chat.id}", 1, 30):
            app.rate_hit(f"err:{chat.id}")
            await ctx.bot.send_message(chat.id, "⚠️ Une erreur est survenue, réessayez dans un instant.")
    except Exception:
        pass


async def _heartbeat(app: App) -> None:
    """Fichier horodaté toutes les 30 s : permet une surveillance externe (HEALTHCHECK Docker, supervision)."""
    path = app.cfg.data_dir / "heartbeat"
    while True:
        try:
            path.write_text(str(int(time.time())), encoding="utf-8")
        except OSError:
            pass
        await asyncio.sleep(30)


async def _notify_interrupted(app: App) -> None:
    """Au démarrage : prévient les utilisateurs dont la session a été coupée par un arrêt / redémarrage du bot."""
    lost = app.store.interrupted_sessions()
    app.store.clear_sessions()
    for uid, info in lost.items():
        kind = "d'auto-trading" if info.get("kind") == "auto" else "de signaux"
        try:
            await app.bot.send_message(info.get("chat", uid), "♻️ <b>Le bot a redémarré</b> : votre session "
                                       f"{kind} a été interrompue.\n"
                                       "Les trades déjà ouverts vont à leur terme chez le courtier. Relancez quand vous voulez.",
                                       parse_mode="HTML", reply_markup=M([[B("🏠 Menu", "menu")]]))
        except TelegramError:
            pass
        await asyncio.sleep(0.1)


def build_application(cfg: Config, store: Store) -> Application:
    from telegram.request import HTTPXRequest
    app = App(cfg, store)

    async def post_init(a: Application):
        app.bot = a.bot

        def loop_error(loop, context):                          # exception dans une tâche d'arrière-plan : journalisée, jamais fatale
            log.error("tâche en erreur : %s", context.get("message"), exc_info=context.get("exception"))
        asyncio.get_running_loop().set_exception_handler(loop_error)
        if not app.bot_username:
            try:
                app.bot_username = (await a.bot.get_me()).username or ""
            except TelegramError:
                log.warning("nom d'utilisateur du bot introuvable (lien de parrainage indisponible)")
        app.tasks = [asyncio.create_task(_heartbeat(app)), asyncio.create_task(_notify_interrupted(app))]

    async def post_shutdown(a: Application):
        for t in getattr(app, "tasks", []):
            t.cancel()
        await app.shutdown()

    tg = (Application.builder().token(cfg.telegram_token).post_init(post_init).post_shutdown(post_shutdown)
          .concurrent_updates(True)
          .request(HTTPXRequest(connection_pool_size=64, connect_timeout=10, read_timeout=20, write_timeout=20, pool_timeout=15))
          .get_updates_request(HTTPXRequest(connection_pool_size=4, connect_timeout=10, read_timeout=35, write_timeout=20,
                                            pool_timeout=15))
          .build())
    tg.bot_data["app"] = app
    tg.add_handler(CommandHandler(["start", "menu"], serialized(cmd_start)))
    tg.add_handler(CommandHandler("stop", serialized(cmd_stop)))
    tg.add_handler(CommandHandler("ssid", serialized(cmd_ssid)))
    tg.add_handler(CommandHandler("id", cmd_id))
    tg.add_handler(CommandHandler("version", cmd_version))
    tg.add_handler(CallbackQueryHandler(serialized(on_callback)))
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, serialized(on_text)))
    tg.add_error_handler(on_error)
    return tg
