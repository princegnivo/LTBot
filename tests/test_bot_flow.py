"""Parcours complet du bot avec un faux Telegram (stub) + simulateur de courtier. Aucun réseau."""
import asyncio
import dataclasses
import sys
import tempfile
import time
import types
from pathlib import Path


def install_stub():
    tg = types.ModuleType("telegram")
    err = types.ModuleType("telegram.error")
    ext = types.ModuleType("telegram.ext")

    class TelegramError(Exception): pass
    class BadRequest(TelegramError): pass
    class RetryAfter(TelegramError):
        retry_after = 1
    class Forbidden(TelegramError): pass
    class NetworkError(TelegramError): pass
    class TimedOut(NetworkError): pass
    err.TelegramError, err.BadRequest, err.RetryAfter = TelegramError, BadRequest, RetryAfter
    err.Forbidden, err.NetworkError, err.TimedOut = Forbidden, NetworkError, TimedOut

    class Btn:
        def __init__(self, text, url=None, callback_data=None):
            self.text, self.url, self.callback_data = text, url, callback_data
    class Markup:
        def __init__(self, rows): self.rows = rows
    class RKM(Markup):
        def __init__(self, rows, **kw): self.rows, self.kw = rows, kw
    tg.InlineKeyboardButton, tg.InlineKeyboardMarkup, tg.Update, tg.ReplyKeyboardMarkup = Btn, Markup, object, RKM
    class Any:
        def __init__(self, *a, **k): pass
        def __getattr__(self, n): return lambda *a, **k: self
    class Filters:
        def __invert__(self): return self
    ext.Application = ext.CallbackQueryHandler = ext.CommandHandler = ext.MessageHandler = Any
    ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    ext.filters = types.SimpleNamespace(TEXT=Filters(), COMMAND=Filters())
    tg.error, tg.ext = err, ext
    sys.modules.update({"telegram": tg, "telegram.error": err, "telegram.ext": ext})


try:
    import telegram  # noqa
except ImportError:
    install_stub()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import app as A  # noqa: E402
from broker import PaperBroker  # noqa: E402
from config import Config  # noqa: E402
from engine import Deal, Position  # noqa: E402
from store import Store  # noqa: E402
from strategies import REGISTRY  # noqa: E402
from strategies.signal import Signal  # noqa: E402

ADMIN, USER, FRIEND = 1, 42, 77
SSID_DEMO = '42["auth",{"session":"abc123def","isDemo":1,"uid":7,"platform":2}]'
SSID_REAL = '42["auth",{"session":"zzz999xyz","isDemo":0,"uid":7,"platform":2}]'
A.make_broker = lambda kind, ssid: PaperBroker(ssid)        # « pocketoption » simulé : aucun réseau


class FakeBot:
    def __init__(self):
        self.sent, self.member, self.n, self.photos = [], True, 0, []

    async def send_message(self, chat_id, text, **kw):
        self.n += 1
        m = FakeMsg(self, chat_id, text, kw.get("reply_markup"), self.n)
        self.sent.append(m)
        return m

    async def send_photo(self, chat_id, photo, **kw):
        self.photos.append(chat_id)

    async def get_chat_member(self, chat, uid):
        return types.SimpleNamespace(status="member" if self.member else "left")

    async def get_me(self): return types.SimpleNamespace(username="MyBot")


class FakeMsg:
    def __init__(self, bot, chat, text, markup, mid=0):
        self.bot, self.chat, self.text, self.markup, self.message_id = bot, chat, text, markup, mid
        self.deleted, self.edits = False, 0
    async def edit_text(self, text, **kw):
        assert not self.deleted, "édition d'un message supprimé"
        self.text, self.markup, self.edits = text, kw.get("reply_markup"), self.edits + 1
    async def delete(self): self.deleted = True
    async def reply_text(self, text, **kw): return await self.bot.send_message(self.chat, text, **kw)


class GoneMsg(FakeMsg):
    async def edit_text(self, text, **kw): raise A.BadRequest("Message to edit not found")


class FakeQ:
    def __init__(self, data, msg): self.data, self.message, self.answers = data, msg, []
    async def answer(self, *a, **k): self.answers.append(a[0] if a else None)
    async def edit_message_text(self, text, **kw): self.message.text, self.message.markup = text, kw.get("reply_markup")


def cbs(msg): return [b.callback_data for row in msg.markup.rows for b in row if b.callback_data]
def urls(msg): return [b.url for row in msg.markup.rows for b in row if b.url]
def labels(msg): return [b.text for row in msg.markup.rows for b in row]


class World:
    def __init__(self, tmp, **cfg_kw):
        base = dict(admin_ids=frozenset({ADMIN}), channel_id="@chan", channel_url="https://t.me/chan",
                    support_url="https://t.me/support", bot_username="MyBot", require_verified=False,
                    promo_code="50START", po_register_url="https://example.test/register?code=50START")
        base.update(cfg_kw)
        self.cfg = Config("x", frozenset(), Path(tmp), "pocketoption", False, 20.0, "feedsession", "", **base)
        self.store = Store(tmp)
        self.app = A.App(self.cfg, self.store)
        self.bot = FakeBot()
        self.app.bot = self.bot
        self.data = {}

    def ctx(self, uid):
        return types.SimpleNamespace(application=types.SimpleNamespace(bot_data={"app": self.app}),
                                     user_data=self.data.setdefault(uid, {}), args=[])

    def upd(self, uid, data=None, text=None, msg=None, name="Bob", username=None):
        chat = types.SimpleNamespace(id=uid, send_message=lambda t, **k: self.bot.send_message(uid, t, **k))
        u = types.SimpleNamespace(id=uid, full_name=name, username=username or f"u{uid}")
        m = msg or FakeMsg(self.bot, uid, text or "", None, 999)
        return types.SimpleNamespace(callback_query=FakeQ(data, m) if data else None, effective_user=u,
                                     effective_chat=chat, effective_message=m)

    async def tap(self, uid, data, msg=None):
        msg = msg or FakeMsg(self.bot, uid, "", None, 998)
        up = self.upd(uid, data=data, msg=msg)
        await A.on_callback(up, self.ctx(uid))
        return msg, up.callback_query

    async def say(self, uid, text):
        up = self.upd(uid, text=text)
        await A.on_text(up, self.ctx(uid))
        return up.effective_message

    async def start(self, uid, args=None):
        ctx = self.ctx(uid); ctx.args = args or []
        up = self.upd(uid, text="/start")
        await A.cmd_start(up, ctx)
        return up.effective_message

    def last(self, uid):
        return [m for m in self.bot.sent if m.chat == uid and not m.deleted and m.text != "⌨️"][-1]   # hors message porteur du clavier

    async def until(self, cond, timeout=4.0):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            await asyncio.sleep(0.05)
        return cond()

    async def wait_end(self, rt, timeout=6.0):
        t0 = time.time()
        while rt.engine.running and time.time() - t0 < timeout:
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6)


def sig(asset="EURUSD_otc", d="call", strat="5s"):
    return Signal(strat, asset, d, 80, REGISTRY[strat].expiration, "test", 1.0)


def set_settings(w, uid, **kw):
    s = w.app.settings(uid)
    for k, v in kw.items():
        setattr(s, k, v)
    w.store.save_settings(uid, s)


# =============================================================================================== accès
async def test_open_access_and_referral(w):
    await w.start(USER)
    assert w.app.is_allowed(USER) and w.store.has_account(USER) and "Jetons :" in w.last(USER).text
    await w.start(99)                                               # n'importe qui peut démarrer, sans autorisation
    assert w.app.is_allowed(99) and "Jetons :" in w.last(99).text
    bal = w.store.tokens(USER)
    await w.start(FRIEND, ["ref_42"])                               # nouvel ami par lien -> le parrain gagne ses jetons
    assert w.store.tokens(USER) == bal + 10 and any(m.chat == USER and "Un ami a rejoint" in m.text for m in w.bot.sent)
    await w.start(FRIEND, ["ref_42"])
    assert w.store.tokens(USER) == bal + 10, "pas de double crédit"
    await w.start(USER, ["ref_42"])
    assert w.store.tokens(USER) == bal + 10, "pas d'auto-parrainage"
    await w.start(99, ["ref_42"])
    assert w.store.tokens(USER) == bal + 10, "un compte déjà existant ne rapporte rien"
    await w.start(78, ["ref_555555"])                               # parrain inconnu : le compte est créé, personne n'est crédité
    assert w.app.is_allowed(78) and w.store.tokens(78) == 0
    # blocage par un admin (seul cas de refus) puis déblocage
    await w.tap(ADMIN, "adm:acc:99")
    assert not w.app.is_allowed(99)
    await w.start(99)
    assert "suspendu" in w.last(99).text
    _, q = await w.tap(99, "menu")
    assert q.answers == ["Accès refusé"]
    await w.tap(ADMIN, "adm:acc:99")
    assert w.app.is_allowed(99)
    w.store.add_tokens(USER, -w.store.tokens(USER))
    print("✓ accès ouvert à tous + parrainage")


async def test_menus(w):
    msg, _ = await w.tap(USER, "menu")
    assert {"tok", "fr", "chan"} <= set(cbs(msg)) and "adm" not in cbs(msg), cbs(msg)
    assert "https://t.me/support" in urls(msg) and "💎 Jetons : <b>0</b>" in msg.text
    assert "Sans SSID : signaux uniquement" in msg.text
    lab = labels(msg)
    for want in ("🚀 Lancer l'auto-trading", "🎮 Mode manuel", "🔒📈 Passer en RÉEL", "📊 Mon compte", "👥 Amis",
                 "🎁 Bonus (5 💎)", "💎 Jetons", "⚙️ Paramètres", "💰 Dépôt", "📢 Canal principal", "❓ FAQ", "🆘 Support"):
        assert want in lab, (want, lab)
    assert "Mode : <b>DÉMO</b>" in msg.text and "🤖" in msg.text
    msg, _ = await w.tap(ADMIN, "menu")
    assert "adm" in cbs(msg) and "tok" not in cbs(msg) and "Jetons" not in msg.text
    assert "🛠 Administration" in labels(msg) and "📡 Signaux" in labels(msg)
    print("✓ menus non-admin (maquette) / admin")


async def test_ssid_gates_trading_but_not_signals(w):
    for entry in ("auto", "man", "auto:again"):
        msg, _ = await w.tap(USER, entry)
        assert "SSID DÉMO requis" in msg.text and {"set:ss", "sig"} <= set(cbs(msg)), (entry, msg.text)
    assert not (w.app.rt(USER).engine and w.app.rt(USER).engine.running)
    # signaux : disponibles sans SSID grâce au flux partagé en lecture seule
    panel, _ = await w.tap(USER, "sig")
    for step in ("sig:as:auto", "sig:st:ok"):
        panel, _ = await w.tap(USER, step, panel)
    rt = w.app.rt(USER)
    assert rt.engine and rt.engine.running and rt.engine.kind == "signals" and rt.broker_mode == "feed"
    assert await rt.broker.balance() == 0.0, "le solde du compte du flux ne fuit jamais"
    try:
        await rt.broker.place("EURUSD_otc", "call", 1, 5)
        raise AssertionError("le flux partagé a passé un ordre !")
    except RuntimeError:
        pass
    assert "Signaux" in rt.dash_msg.text
    await w.tap(USER, "run:stop")
    await w.wait_end(rt)
    assert "Scan arrêté" in w.last(USER).text
    print("✓ sans SSID : pas d'ordres, signaux oui")


async def test_ssid_in_settings(w):
    msg, _ = await w.tap(USER, "set")
    assert "set:ss" in cbs(msg)
    msg, _ = await w.tap(USER, "set:ss")
    assert "🟠 Démo : ❌ absent" in msg.text and "🟢 Réel : ❌ absent" in msg.text
    assert {"set:ss:demo", "set:ss:real"} <= set(cbs(msg)) and not [c for c in cbs(msg) if "test" in c]
    msg, _ = await w.tap(USER, "set:ss:demo", msg)
    assert "SSID DÉMO" in msg.text and w.data[USER]["await"] == ("ssid", "demo")
    user_msg = await w.say(USER, "n'importe quoi")                                   # invalide : on reste en attente
    assert user_msg.deleted and w.data[USER].get("await") == ("ssid", "demo") and "SSID refusé" in msg.text
    await w.say(USER, SSID_REAL)                                                     # SSID réel dans la case démo
    assert w.store.get_ssid(USER, "demo") == "" and "RÉEL" in msg.text
    user_msg = await w.say(USER, "```\n" + SSID_DEMO.replace('"', "”") + "\n```")    # guillemets iPhone + bloc de code
    assert user_msg.deleted and w.store.get_ssid(USER, "demo") == SSID_DEMO and "await" not in w.data[USER]
    screen = w.last(USER)
    assert "🟠 Démo : ✅ vérifié · compte #7" in screen.text and "🟢 Réel : ❌ absent" in screen.text
    assert "set:ss:test:demo" in cbs(screen) and "set:ss:del:demo" in cbs(screen)
    assert "connecté" in screen.text or "enregistré" in screen.text
    assert w.store.ssid_meta(USER, "demo")["po_uid"] == 7 and w.store.ssid_meta(USER, "demo")["verified"] > 0
    # si le message ne peut pas être supprimé, l'utilisateur est prévenu
    await asyncio.sleep(1.6)
    await w.tap(USER, "set:ss:real", screen)
    up = w.upd(USER, text=SSID_REAL)
    async def boom(): raise A.TelegramError("trop ancien")
    up.effective_message.delete = boom
    await A.on_text(up, w.ctx(USER))
    assert w.store.get_ssid(USER, "real") == SSID_REAL
    screen = w.last(USER)
    assert "Supprimez vous-même votre message" in screen.text and "🟢 Réel : ✅ enregistré" in screen.text
    assert "verrouillé" in screen.text, "mode réel verrouillé : l'utilisateur est prévenu"
    # test de connexion
    msg, _ = await w.tap(USER, "set:ss:test:demo", screen)
    assert "SSID DÉMO valide" in msg.text and "vérifié" in msg.text
    msg, _ = await w.tap(USER, "set:ss:test:real", msg)
    assert "verrouillé" in msg.text
    # envois rapprochés : le 2e est refusé proprement (anti double envoi)
    await w.tap(USER, "set:ss:demo", msg)
    await w.say(USER, SSID_DEMO)
    await w.tap(USER, "set:ss:demo")
    await w.say(USER, SSID_DEMO)
    assert "déjà en cours" in w.data[USER]["prompt"].text or w.data[USER].get("await") is None
    await asyncio.sleep(1.6)
    # suppression
    msg, _ = await w.tap(USER, "set:ss:del:real", w.last(USER))
    assert w.store.get_ssid(USER, "real") == "" and "supprimé" in msg.text
    # la commande /ssid fonctionne toujours (et supprime le message)
    ctx = w.ctx(USER); up = w.upd(USER, text=f"/ssid demo {SSID_DEMO}")
    await A.cmd_ssid(up, ctx)
    assert up.effective_message.deleted and "enregistré" in w.bot.sent[-1].text
    w.data[USER].clear()
    print("✓ SSID démo/réel : formats, vérification, test, suppression, avertissement")


async def test_tokens_channel_friends(w):
    msg, _ = await w.tap(USER, "auto:again")                    # SSID présent mais 0 jeton
    assert "Plus de jetons" in msg.text and "tok" in cbs(msg)
    assert not (w.app.rt(USER).engine and w.app.rt(USER).engine.running)
    msg, _ = await w.tap(USER, "tok")
    assert "Vous avez : <b>0</b>" in msg.text and {"bonus", "fr", "dep:check"} <= set(cbs(msg)), cbs(msg)
    assert "Code promo <code>50START</code>" in msg.text
    assert len(urls(msg)) == 5 and "amount=20" in urls(msg)[0] and "amount=500" in urls(msg)[-1]
    assert "💰 $20 ➔ 200 💎" in labels(msg) and "💰 $500 ➔ 10000 💎" in labels(msg)
    assert "Session réelle complète : <b>1</b> · démo : <b>1</b>" in msg.text
    await w.tap(ADMIN, "adm:tk:42:10")
    assert w.store.tokens(USER) == 10
    assert any(m.chat == USER and "10" in m.text and "jetons" in m.text for m in w.bot.sent)
    await w.tap(ADMIN, "adm:tke:42")
    await w.say(ADMIN, "-3")
    assert w.store.tokens(USER) == 7
    # canal : bonus une seule fois, uniquement si le bot constate l'abonnement
    msg, _ = await w.tap(USER, "chan")
    assert "https://t.me/chan" in urls(msg) and "chan:ok" in cbs(msg)
    before = w.store.tokens(USER)
    w.bot.member = False
    msg, _ = await w.tap(USER, "chan:ok")
    assert "pas encore abonné" in msg.text and w.store.tokens(USER) == before
    w.bot.member = True
    msg, _ = await w.tap(USER, "chan:ok")
    assert "+10 jetons" in msg.text and w.store.tokens(USER) == before + 10
    await w.tap(USER, "chan:ok")
    assert w.store.tokens(USER) == before + 10, "bonus unique"
    msg, _ = await w.tap(USER, "fr")
    assert "https://t.me/MyBot?start=ref_42" in msg.text and "Invités : <b>1</b>" in msg.text
    assert any("t.me/share/url" in u for u in urls(msg))
    print("✓ jetons + canal + amis")


async def test_start_is_clean(w):
    m = await w.start(USER)
    assert m.deleted, "la commande /start est supprimée"
    kb = [m for m in w.bot.sent if m.chat == USER and m.text == "⌨️" and not m.deleted]
    assert len(kb) == 1, "le message porteur du clavier est CONSERVÉ (sinon Telegram retire le clavier)"
    await w.start(USER)                                                    # 2e /start : un seul porteur reste
    kb = [m for m in w.bot.sent if m.chat == USER and m.text == "⌨️" and not m.deleted]
    assert len(kb) == 1, "jamais deux porteurs"
    assert "Jetons :" in w.last(USER).text
    print("✓ /start propre")


# =============================================================================================== trades en direct
async def test_live_trade_messages(w):
    rt = w.app.rt(USER); rt.chat_id = USER
    notify = w.app.make_notify(rt)
    eng = types.SimpleNamespace(kind="auto")
    info = REGISTRY["5s"]
    base = lambda s, d: dict(sig=s, info=info, payout=92, trend={"label": "baissière", "dir": "down"},
                             name="EUR/USD OTC", deal=d, sid=None)
    d1 = Deal(1, "EURUSD_otc", "call", "5s", 1.0, 92, 2)
    await notify("signal", eng, **base(sig(), d1))
    m1 = rt.by_deal[1].msg
    assert "ACTIF" in m1.text and "ACHAT" in m1.text and "envoi de l'ordre" in m1.text
    d1.pos = Position(1, 1, d1.asset, "call", 1.0, 2, "5s", status="open", opened_at=time.time())
    await notify("position_open", eng, deal=d1, pos=d1.pos)
    await asyncio.sleep(1.3)
    assert "▰" in m1.text and m1.edits >= 1, m1.text
    d1.pos.status, d1.pos.profit = "win", 0.92
    await notify("position_closed", eng, deal=d1, pos=d1.pos)
    assert "GAIN +$0.92" in m1.text and not m1.deleted and rt.by_deal[1].state == "done"
    d2 = Deal(2, "EURUSD_otc", "put", "5s", 1.0, 92, 2)
    await notify("signal", eng, **base(sig(d="put"), d2))
    m2 = rt.by_deal[2].msg
    assert m1.deleted and not m2.deleted and "VENTE" in m2.text, "le précédent est supprimé juste avant le suivant"
    d3 = Deal(3, "EURUSD_otc", "call", "1M", 1.0, 92, 60); d3.strategy = "1M"
    await notify("signal", eng, **base(sig(strat="1M"), d3))
    assert not m2.deleted, "un trade en cours n'est jamais supprimé"
    # un trade dont le résultat s'affiche (état « closing ») est protégé contre le nettoyage
    rt.by_deal[2].state = "closing"
    await w.app.clean(rt, keep=rt.by_deal[3])
    assert not m2.deleted
    rt.by_deal[2].state = "running"
    d2.pos = Position(2, 1, d2.asset, "put", 1.0, 2, "5s", status="loss", profit=-1.0)
    await notify("position_closed", eng, deal=d2, pos=d2.pos)
    assert "PERTE −$1.00" in m2.text
    # message disparu côté Telegram : plus d'avertissement, plus d'édition fantôme
    assert await w.app.edit(rt, GoneMsg(w.bot, USER, "x", None), "y") == "gone"
    await w.app.clean(rt, everything=True)
    print("✓ messages de trade : animation, suppression avant le suivant, anti-course, message disparu")


# =============================================================================================== sessions
async def test_auto_series_flow_and_dashboard(w):
    w.store.add_tokens(USER, 5)
    before = w.store.tokens(USER)
    panel, _ = await w.tap(USER, "auto")
    for step in ("auto:stake:1", "auto:as:auto", "auto:st:ok", "auto:md:series"):
        panel, _ = await w.tap(USER, step, panel)
    assert {"auto:sr:5", "auto:sr:7", "auto:sr:10"} <= set(cbs(panel))
    assert not [c for c in cbs(panel) if c.startswith(("auto:tp", "auto:sl"))], "série : ni TP ni SL"
    # solde insuffisant : message clair, aucun jeton consommé
    set_settings(w, USER, stake=60000.0)
    msg, _ = await w.tap(USER, "auto:sr:5", panel)
    assert "Solde DÉMO insuffisant" in msg.text and w.store.tokens(USER) == before
    assert not (w.app.rt(USER).engine and w.app.rt(USER).engine.running)
    set_settings(w, USER, stake=1.0)
    panel, _ = await w.tap(USER, "auto:sr:7")
    rt = w.app.rt(USER)
    s = w.app.settings(USER)
    assert (s.session_mode, s.series_deals) == ("series", 7)
    assert rt.engine and rt.engine.running and panel.deleted, "l'écran de config est remplacé par le déroulé"
    assert w.store.tokens(USER) == before - 1, "1 jeton par session"
    dash = rt.dash_msg
    assert "Auto-trading" in dash.text and "Série · deal 0/7" in dash.text and "TP" not in dash.text and "SL" not in dash.text
    assert "run:stop" in cbs(dash)
    msg, _ = await w.tap(USER, "auto:again")
    assert "déjà en cours" in msg.text and w.store.tokens(USER) == before - 1
    # un deal simulé : le déroulé s'anime et se met à jour dans la même bulle
    d = Deal(1, "EURUSD_otc", "call", "5s", 1.0, 92, 3, cycle=1)
    d.pos = Position(1, 1, d.asset, "call", 1.0, 3, "5s", status="open", opened_at=time.time())
    rt.engine.deals.append(d); rt.engine.open_deals[1] = d
    rt.engine._emit("position_open", deal=d, pos=d.pos)
    await asyncio.sleep(2.2)
    assert "Deal 1·Étape 1" in dash.text and "trade en cours" in dash.text and dash.edits >= 2, dash.text
    rt.engine.open_deals.pop(1); d.status = "aborted"
    # arrêt -> le déroulé reste, le résultat arrive ensuite avec les 3 boutons
    _, q = await w.tap(USER, "run:stop")
    assert q.answers and "Arrêt" in q.answers[0]
    await w.wait_end(rt)
    summ = w.last(USER)
    assert not dash.deleted and "🏁 Terminé" in dash.text, "le déroulé complet reste affiché"
    assert "Résultat de la session" in summ.text and cbs(summ) == ["real", "auto:again", "menu"], cbs(summ)
    assert w.bot.sent.index(summ) > w.bot.sent.index(dash), "le résultat vient après le déroulé"
    # « Je veux ça en réel » : verrouillé tant que ALLOW_REAL=0
    msg, _ = await w.tap(USER, "real", summ)
    assert "verrouillé" in msg.text and w.app.settings(USER).mode == "demo"
    # déverrouillé mais sans SSID réel -> demande de l'enregistrer
    w.app.cfg = dataclasses.replace(w.cfg, allow_real=True)
    msg, _ = await w.tap(USER, "real", summ)
    assert "SSID RÉEL requis" in msg.text and "set:ss" in cbs(msg)
    # avec SSID réel : bascule + plafond de mise ; Relancer coûte 1 jeton ; l'ancien déroulé est supprimé
    w.store.set_ssid(USER, "real", SSID_REAL)
    set_settings(w, USER, stake=50.0)
    msg, _ = await w.tap(USER, "real", summ)
    st = w.app.settings(USER)
    assert st.mode == "real" and "RÉEL activé" in msg.text and st.stake == 20.0, (st.mode, st.stake, msg.text)
    assert cbs(msg) == ["auto:again", "menu"]
    t0 = w.store.tokens(USER)
    await w.tap(USER, "auto:again", msg)
    assert rt.engine.running and rt.engine.cfg.mode == "real" and w.store.tokens(USER) == t0 - 1
    assert dash.deleted, "le déroulé précédent est supprimé au relancement"
    await w.tap(USER, "run:stop")
    await w.wait_end(rt)
    assert "real" not in cbs(w.last(USER)) and cbs(w.last(USER)) == ["auto:again", "menu"]
    w.store.del_ssid(USER, "real")
    set_settings(w, USER, mode="demo")
    print("✓ auto : série 5/7/10 sans SL/TP, solde, déroulé animé conservé, réel, relance")


async def test_tp_mode_still_asks_tp_and_sl(w):
    set_settings(w, ADMIN, stake=1.0)
    panel, _ = await w.tap(ADMIN, "auto")
    for step in ("auto:stake:1", "auto:as:auto", "auto:st:ok", "auto:md:tp"):
        panel, _ = await w.tap(ADMIN, step, panel)
    assert "Take-profit" in panel.text
    panel, _ = await w.tap(ADMIN, "auto:tp:20", panel)
    assert "Stop-loss" in panel.text
    await w.tap(ADMIN, "auto:sl:30", panel)
    rt = w.app.rt(ADMIN)
    assert rt.engine.running and "TP +$20" in rt.dash_msg.text and "SL −$30" in rt.dash_msg.text
    assert w.store.tokens(ADMIN) == 0, "l'admin n'utilise jamais de jetons"
    await w.tap(ADMIN, "run:stop")
    await w.wait_end(rt)
    print("✓ mode take-profit : TP puis SL, admin sans jetons")


async def test_manual_flow(w):
    set_settings(w, USER, min_payout=90, mode="demo")
    msg, _ = await w.tap(FRIEND, "man")
    assert "SSID DÉMO requis" in msg.text, "pas de SSID : pas de trade manuel"
    msg, _ = await w.tap(USER, "man")
    msg, _ = await w.tap(USER, "man:mk:otc", msg)
    msg, _ = await w.tap(USER, "man:ct:otc:currency", msg)
    pick = [c for c in cbs(msg) if c.startswith("man:pk:")][0]
    msg, _ = await w.tap(USER, pick, msg)
    rt = w.app.rt(USER)
    assert rt.manual and rt.manual.running and "En attente d'un signal" in msg.text and "man:stk:5" in cbs(msg)
    sym = pick.split(":", 2)[2]
    DF = __import__("tests.test_engine", fromlist=["DF"]).DF
    rt.manual.on_signal(sig(sym, "put", "5s"), REGISTRY["5s"], DF, time.perf_counter(), (sym, "5s", 1))
    await w.until(lambda: "VENTE" in w.last(USER).text)
    sm = w.last(USER)
    assert [c for c in cbs(sm) if c.startswith("man:go:")] and "VENTE" in sm.text and "en attente de votre ordre" in sm.text, \
        (sm.text, [m.text[:60] for m in w.bot.sent[-4:]])
    rt.manual.on_signal(sig(sym, "call", "5s"), REGISTRY["5s"], DF, time.perf_counter(), (sym, "5s", 2))
    await w.until(lambda: sm.deleted and "ACHAT" in w.last(USER).text)
    assert sm.deleted
    sm2 = w.last(USER)
    go2 = [c for c in cbs(sm2) if c.startswith("man:go:")][0]
    _, q = await w.tap(USER, go2)
    assert q.answers and "Ordre envoyé" in q.answers[0]
    await asyncio.sleep(1.5)
    assert "Résultat" in sm2.text and ("▰" in sm2.text or "envoi" in sm2.text)
    await asyncio.sleep(6.5)
    assert ("GAIN" in sm2.text or "PERTE" in sm2.text or "ÉGALITÉ" in sm2.text), sm2.text
    assert cbs(sm2) == ["man", "menu"]
    _, q = await w.tap(USER, go2)
    assert q.answers and "⛔" in q.answers[0]
    await w.tap(USER, "menu")
    assert rt.manual is None
    print("✓ manuel : SSID requis, signal + bouton + résultat dans le même message")


async def test_admin_panel(w):
    msg, _ = await w.tap(USER, "adm")
    assert msg.text == "", "écran admin jamais affiché à un non-admin"
    msg, _ = await w.tap(ADMIN, "adm")
    assert "Administration" in msg.text and {"adm:u:0", "adm:add", "adm:stats", "adm:bc"} <= set(cbs(msg))
    msg, _ = await w.tap(ADMIN, "adm:u:0")
    assert "adm:usr:42" in cbs(msg)
    msg, _ = await w.tap(ADMIN, "adm:usr:42")
    assert {"adm:tk:42:10", "adm:tk:42:50", "adm:tk:42:100", "adm:tke:42", "adm:acc:42"} <= set(cbs(msg))
    assert "🚫 Bloquer l'utilisateur" in labels(msg)
    await w.tap(ADMIN, "adm:add")
    await w.say(ADMIN, "abc")
    assert "adm_add" in str(w.data[ADMIN].get("await")), "saisie invalide : on reste en attente"
    await w.say(ADMIN, "555 25")
    assert w.app.is_allowed(555) and w.store.tokens(555) == 25
    assert any(m.chat == 555 and "compte est prêt" in m.text for m in w.bot.sent)
    msg, _ = await w.tap(ADMIN, "adm:stats")
    assert "Stats globales" in msg.text
    await w.tap(ADMIN, "adm:bc")
    await w.say(ADMIN, "Maintenance ce soir")
    prev = w.last(ADMIN)
    assert "Aperçu" in prev.text and "adm:bcok" in cbs(prev)
    n = len(w.bot.sent)
    msg, _ = await w.tap(ADMIN, "adm:bcok", prev)
    assert any(m.chat == 555 and m.text == "Maintenance ce soir" for m in w.bot.sent[n:]) and "Message envoyé" in msg.text
    print("✓ panneau admin")


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        w = World(tmp)
        for t in (test_open_access_and_referral, test_menus, test_ssid_gates_trading_but_not_signals,
                  test_ssid_in_settings, test_tokens_channel_friends, test_start_is_clean, test_live_trade_messages,
                  test_auto_series_flow_and_dashboard, test_tp_mode_still_asks_tp_and_sl, test_manual_flow,
                  test_admin_panel):
            await t(w)
        await w.app.shutdown()
        leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and "dash_loop" in repr(t)]
        assert not leftovers, f"tâche dash_loop encore en attente à la fermeture : {leftovers}"
    print("parcours bot OK")


if __name__ == "__main__":
    asyncio.run(main())
