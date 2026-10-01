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
    err.TelegramError, err.BadRequest, err.RetryAfter = TelegramError, BadRequest, RetryAfter

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
from bot import ui  # noqa: E402
from config import Config  # noqa: E402
from engine import Deal, Position  # noqa: E402
from store import Store  # noqa: E402
from strategies import REGISTRY  # noqa: E402
from strategies.signal import Signal  # noqa: E402

ADMIN, USER = 1, 42
SSID_DEMO = '42["auth",{"session":"abc123","isDemo":1,"uid":7,"platform":2}]'
SSID_REAL = '42["auth",{"session":"zzz999","isDemo":0,"uid":7,"platform":2}]'


class FakeBot:
    def __init__(self):
        self.sent, self.member, self.fail_chat = [], True, set()
        self.n = 0

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self.fail_chat:
            raise A.TelegramError("blocked")
        self.n += 1
        m = FakeMsg(self, chat_id, text, kw.get("reply_markup"), self.n)
        self.sent.append(m)
        return m

    async def get_chat_member(self, chat, uid):
        return types.SimpleNamespace(status="member" if self.member else "left")

    async def get_me(self): return types.SimpleNamespace(username="MyBot")


class FakeMsg:
    def __init__(self, bot, chat, text, markup, mid=0, user_text=False):
        self.bot, self.chat, self.text, self.markup, self.message_id = bot, chat, text, markup, mid
        self.deleted, self.edits = False, 0
    async def edit_text(self, text, **kw):
        assert not self.deleted, "édition d'un message supprimé"
        self.text, self.markup, self.edits = text, kw.get("reply_markup"), self.edits + 1
    async def delete(self): self.deleted = True
    async def reply_text(self, text, **kw): return await self.bot.send_message(self.chat, text, **kw)


class FakeQ:
    def __init__(self, data, msg): self.data, self.message, self.answers = data, msg, []
    async def answer(self, *a, **k): self.answers.append(a[0] if a else None)
    async def edit_message_text(self, text, **kw): self.message.text, self.message.markup = text, kw.get("reply_markup")


def cbs(msg): return [b.callback_data for row in msg.markup.rows for b in row if b.callback_data]
def urls(msg): return [b.url for row in msg.markup.rows for b in row if b.url]
def labels(msg): return [b.text for row in msg.markup.rows for b in row]


class World:
    def __init__(self, tmp):
        self.cfg = Config("x", frozenset({USER}), Path(tmp), "paper", False, 20.0, "", "",
                          admin_ids=frozenset({ADMIN}), channel_id="@chan", channel_url="https://t.me/chan",
                          support_url="https://t.me/support", bot_username="MyBot")
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
        up = types.SimpleNamespace(callback_query=FakeQ(data, m) if data else None, effective_user=u,
                                   effective_chat=chat, effective_message=m)
        return up

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
        return [m for m in self.bot.sent if m.chat == uid and not m.deleted][-1]


def sig(asset="EURUSD_otc", d="call", strat="5s"):
    return Signal(strat, asset, d, 80, REGISTRY[strat].expiration, "test", 1.0)


async def test_menus_and_tokens(w):
    # --- menu non-admin : Jetons / Canal / Amis / Support (lien) ; pas d'admin
    msg, _ = await w.tap(USER, "menu")
    assert {"tok", "fr", "chan"} <= set(cbs(msg)) and "adm" not in cbs(msg), cbs(msg)
    assert "https://t.me/support" in urls(msg) and "💎 Jetons : <b>0</b>" in msg.text
    # --- menu admin : bouton administration, pas de jetons
    msg, _ = await w.tap(ADMIN, "menu")
    assert "adm" in cbs(msg) and "tok" not in cbs(msg) and "Jetons" not in msg.text
    # --- sans jetons : l'auto-trading est refusé, les signaux/manuel restent gratuits
    msg, _ = await w.tap(USER, "auto:again")
    assert "Plus de jetons" in msg.text and "tok" in cbs(msg)
    assert not (w.app.rt(USER).engine and w.app.rt(USER).engine.running)
    msg, _ = await w.tap(USER, "tok")
    assert "Vous avez : <b>0</b>" in msg.text and "chan" in cbs(msg) and "fr" in cbs(msg)
    # --- l'admin ajoute des jetons (bouton rapide + saisie libre)
    await w.tap(ADMIN, "adm:tk:42:10")
    assert w.store.tokens(USER) == 10
    assert any(m.chat == USER and "+" not in m.text and "10" in m.text and "jetons" in m.text for m in w.bot.sent)
    await w.tap(ADMIN, "adm:tke:42")
    await w.say(ADMIN, "-3")
    assert w.store.tokens(USER) == 7
    print("✓ menus + jetons")


async def test_channel_and_friends(w):
    msg, _ = await w.tap(USER, "chan")
    assert "https://t.me/chan" in urls(msg) and "chan:ok" in cbs(msg)
    before = w.store.tokens(USER)
    w.bot.member = False
    msg, _ = await w.tap(USER, "chan:ok")
    assert "pas encore abonné" in msg.text and w.store.tokens(USER) == before
    w.bot.member = True
    msg, _ = await w.tap(USER, "chan:ok")
    assert "+10 jetons" in msg.text and w.store.tokens(USER) == before + 10
    msg, _ = await w.tap(USER, "chan:ok")
    assert w.store.tokens(USER) == before + 10, "bonus unique"
    # --- amis : lien de parrainage + invité autorisé automatiquement, +10 au parrain (une seule fois)
    msg, _ = await w.tap(USER, "fr")
    assert "https://t.me/MyBot?start=ref_42" in msg.text and "Invités : <b>0</b>" in msg.text
    assert any("t.me/share/url" in u for u in urls(msg))
    bal = w.store.tokens(USER)
    stranger = await w.start(99)
    assert "Accès refusé" in w.last(99).text and not w.app.is_allowed(99)
    await w.start(77, ["ref_42"])
    assert w.app.is_allowed(77) and w.store.tokens(USER) == bal + 10
    assert any(m.chat == USER and "Un ami a rejoint" in m.text for m in w.bot.sent)
    await w.start(77, ["ref_42"])
    assert w.store.tokens(USER) == bal + 10, "pas de double crédit"
    await w.start(USER, ["ref_42"])
    assert w.store.tokens(USER) == bal + 10, "pas d'auto-parrainage"
    msg, _ = await w.tap(USER, "fr")
    assert "Invités : <b>1</b>" in msg.text and "gagné : <b>10</b>" in msg.text
    print("✓ canal + amis")


async def test_start_is_clean(w):
    m = await w.start(USER)
    assert m.deleted, "la commande /start est supprimée"
    assert w.bot.sent[-2].text == "⌨️" and w.bot.sent[-2].deleted, "message du clavier supprimé (le clavier reste)"
    assert "Menu" in w.last(USER).text
    print("✓ /start propre")


async def test_ssid_in_settings(w):
    msg, _ = await w.tap(USER, "set")
    assert "set:ss" in cbs(msg)
    msg, _ = await w.tap(USER, "set:ss")
    assert "❌ absent" in msg.text and {"set:ss:demo", "set:ss:real"} <= set(cbs(msg))
    msg, _ = await w.tap(USER, "set:ss:demo", msg)
    assert "SSID DÉMO" in msg.text and w.data[USER]["await"] == ("ssid", "demo")
    # SSID invalide : message d'erreur, on reste en attente
    user_msg = await w.say(USER, "n'importe quoi")
    assert user_msg.deleted and w.data[USER].get("await") == ("ssid", "demo")
    assert "SSID refusé" in msg.text
    # SSID réel envoyé dans la case démo : refusé
    await w.say(USER, SSID_REAL)
    assert w.store.get_ssid(USER, "demo") == "" and "RÉEL" in msg.text
    # bon SSID démo : enregistré, message supprimé, écran mis à jour
    user_msg = await w.say(USER, SSID_DEMO)
    assert user_msg.deleted and w.store.get_ssid(USER, "demo") == SSID_DEMO and "await" not in w.data[USER]
    screen = w.last(USER)
    assert "🟠 Démo : ✅ enregistré" in screen.text and "🟢 Réel : ❌ absent" in screen.text and "Remplacer" in labels(screen)[0]
    # SSID réel via Paramètres
    await w.tap(USER, "set:ss:real", screen)
    await w.say(USER, SSID_REAL)
    assert w.store.get_ssid(USER, "real") == SSID_REAL
    assert "verrouillé" in w.last(USER).text, "mode réel verrouillé : l'utilisateur est prévenu"
    # suppression
    msg, _ = await w.tap(USER, "set:ss:del:real", w.last(USER))
    assert w.store.get_ssid(USER, "real") == "" and "supprimé" in msg.text
    # la commande /ssid fonctionne toujours
    ctx = w.ctx(USER); up = w.upd(USER, text=f"/ssid demo {SSID_DEMO}")
    await A.cmd_ssid(up, ctx)
    assert up.effective_message.deleted and "enregistré" in w.bot.sent[-1].text
    print("✓ SSID démo/réel dans les paramètres")


async def test_live_trade_messages(w):
    rt = w.app.rt(USER); rt.chat_id = USER
    notify = w.app.make_notify(rt)
    eng = types.SimpleNamespace(kind="auto")
    info = REGISTRY["5s"]

    def mkdeal(i):
        d = Deal(i, "EURUSD_otc", "call", "5s", 1.0, 92, 2)
        return d
    base = lambda s, d: dict(sig=s, info=info, payout=92, trend={"label": "baissière", "dir": "down"}, name="EUR/USD OTC", deal=d, sid=None)
    d1 = mkdeal(1)
    await notify("signal", eng, **base(sig(), d1))
    m1 = rt.by_deal[1].msg
    assert "ACTIF" in m1.text and "ACHAT" in m1.text and "envoi de l'ordre" in m1.text
    d1.pos = Position(1, 1, d1.asset, "call", 1.0, 2, "5s", status="open", opened_at=time.time())
    await notify("position_open", eng, deal=d1, pos=d1.pos)
    await asyncio.sleep(1.3)
    assert "▰" in m1.text and m1.edits >= 1, m1.text            # animation en cours
    d1.pos.status, d1.pos.profit = "win", 0.92
    await notify("position_closed", eng, deal=d1, pos=d1.pos)
    assert "GAIN +$0.92" in m1.text and not m1.deleted
    # le signal suivant supprime le précédent (terminé) juste avant d'être envoyé
    d2 = mkdeal(2)
    await notify("signal", eng, **base(sig(d="put"), d2))
    m2 = rt.by_deal[2].msg
    assert m1.deleted and not m2.deleted and "VENTE" in m2.text
    # un trade encore en cours n'est jamais supprimé
    d3 = mkdeal(3); d3.strategy = "1M"
    await notify("signal", eng, **base(sig(strat="1M"), d3))
    assert not m2.deleted, "m2 est en cours"
    d2.pos = Position(2, 1, d2.asset, "put", 1.0, 2, "5s", status="loss", profit=-1.0)
    await notify("position_closed", eng, deal=d2, pos=d2.pos)
    assert "PERTE −$1.00" in m2.text
    # fin de session : tout est nettoyé, seul le résumé reste
    rt.dash_msg = None
    e = w.app.rt(USER)
    print("✓ messages de trade en direct + suppression avant le suivant")


async def test_auto_session_and_result_buttons(w):
    s = w.app.settings(USER)
    w.store.add_tokens(USER, 5)
    before = w.store.tokens(USER)
    panel, _ = await w.tap(USER, "auto")
    for step in ("auto:stake:1", "auto:as:auto", "auto:st:ok", "auto:md:series", "auto:tp:20", "auto:sl:30"):
        panel, _ = await w.tap(USER, step, panel)
    rt = w.app.rt(USER)
    assert rt.engine and rt.engine.running and panel.deleted, "l'écran de config est remplacé par la barre d'état"
    assert w.store.tokens(USER) == before - 1, "1 jeton par session"
    dash = rt.dash_msg
    assert "Auto-trading" in dash.text and "run:stop" in cbs(dash)
    # un seul trade à la fois par stratégie / pas de session en double
    msg, _ = await w.tap(USER, "auto:again")
    assert "déjà en cours" in msg.text and w.store.tokens(USER) == before - 1
    # arrêt -> résumé avec les 3 boutons (démo => « Je veux ça en réel »)
    _, q = await w.tap(USER, "run:stop")
    assert q.answers and "Arrêt" in q.answers[0]
    for _ in range(40):
        if not rt.engine.running: break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    summ = w.last(USER)
    assert "Résultat de la session" in summ.text and "Trades lancés" in summ.text
    assert cbs(summ) == ["real", "auto:again", "menu"], cbs(summ)
    assert dash.deleted, "la barre d'état est supprimée à la fin"
    # « Je veux ça en réel » : verrouillé tant que ALLOW_REAL=0
    msg, _ = await w.tap(USER, "real", summ)
    assert "verrouillé" in msg.text and w.app.settings(USER).mode == "demo"
    # déverrouillé : bascule en réel + plafond de mise, puis Relancer (coûte 1 jeton) ; plus de bouton « réel »
    w.app.cfg = dataclasses.replace(w.cfg, allow_real=True)
    tmp_s = w.app.settings(USER); tmp_s.stake = 50.0; w.store.save_settings(USER, tmp_s)
    msg, _ = await w.tap(USER, "real", summ)
    st = w.app.settings(USER)
    assert st.mode == "real" and "RÉEL activé" in msg.text and st.stake == 20.0, (st.mode, st.stake, msg.text)
    assert cbs(msg) == ["auto:again", "menu"]
    t0 = w.store.tokens(USER)
    await w.tap(USER, "auto:again", msg)
    assert rt.engine.running and rt.engine.cfg.mode == "real" and w.store.tokens(USER) == t0 - 1
    await w.tap(USER, "run:stop")
    for _ in range(40):
        if not rt.engine.running: break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    assert "real" not in cbs(w.last(USER)) and cbs(w.last(USER)) == ["auto:again", "menu"]
    tmp_s = w.app.settings(USER); tmp_s.mode = "demo"; w.store.save_settings(USER, tmp_s)
    print("✓ session auto, jetons, boutons Je veux ça en réel / Relancer / Menu")


async def test_admin_runs_without_tokens(w):
    assert w.store.tokens(ADMIN) == 0
    for step in ("auto", "auto:stake:1", "auto:as:auto", "auto:st:ok", "auto:md:series", "auto:tp:20", "auto:sl:30"):
        await w.tap(ADMIN, step)
    rt = w.app.rt(ADMIN)
    assert rt.engine and rt.engine.running and w.store.tokens(ADMIN) == 0
    await w.tap(ADMIN, "run:stop")
    for _ in range(40):
        if not rt.engine.running: break
        await asyncio.sleep(0.1)
    print("✓ l'admin utilise le bot sans jetons")


async def test_manual_flow(w):
    s = w.app.settings(USER)
    s.min_payout = 90; s.mode = "demo"; w.store.save_settings(USER, s)
    msg, _ = await w.tap(USER, "man")
    msg, _ = await w.tap(USER, "man:mk:otc", msg)
    msg, _ = await w.tap(USER, "man:ct:otc:currency", msg)
    pick = [c for c in cbs(msg) if c.startswith("man:pk:")][0]
    msg, _ = await w.tap(USER, pick, msg)
    rt = w.app.rt(USER)
    assert rt.manual and rt.manual.running and "En attente d'un signal" in msg.text and "man:stk:5" in cbs(msg)
    # un signal arrive -> format + bouton « Placer le trade »
    sym = pick.split(":", 2)[2]
    rt.manual.on_signal(sig(sym, "put", "5s"), REGISTRY["5s"], __import__("tests.test_engine", fromlist=["DF"]).DF,
                        time.perf_counter(), (sym, "5s", 1))
    await asyncio.sleep(0.4)
    sm = w.last(USER)
    go = [c for c in cbs(sm) if c.startswith("man:go:")]
    assert go and "VENTE" in sm.text and "en attente de votre ordre" in sm.text, sm.text
    # signal suivant : le précédent (non placé) est supprimé
    rt.manual.on_signal(sig(sym, "call", "5s"), REGISTRY["5s"], __import__("tests.test_engine", fromlist=["DF"]).DF,
                        time.perf_counter(), (sym, "5s", 2))
    await asyncio.sleep(0.4)
    assert sm.deleted
    sm2 = w.last(USER)
    go2 = [c for c in cbs(sm2) if c.startswith("man:go:")][0]
    # le bouton place le trade ; le résultat s'affiche dans le MÊME message
    _, q = await w.tap(USER, go2)
    assert q.answers and "Ordre envoyé" in q.answers[0]
    await asyncio.sleep(1.5)
    assert "Résultat" in sm2.text and ("▰" in sm2.text or "envoi" in sm2.text)
    await asyncio.sleep(6.5)
    assert ("GAIN" in sm2.text or "PERTE" in sm2.text or "ÉGALITÉ" in sm2.text), sm2.text
    assert cbs(sm2) == ["man", "menu"]
    # déjà placé : refusé
    _, q = await w.tap(USER, go2)
    assert q.answers and "⛔" in q.answers[0]
    # retour au menu : le scan manuel s'arrête
    await w.tap(USER, "menu")
    assert rt.manual is None
    print("✓ manuel : signal + bouton + résultat dans le même message")


async def test_admin_panel(w):
    # un non-admin ne voit rien d'admin
    msg, _ = await w.tap(USER, "adm")
    assert msg.text == "", "écran admin jamais affiché à un non-admin"
    msg, _ = await w.tap(ADMIN, "adm")
    assert "Administration" in msg.text and {"adm:u:0", "adm:add", "adm:stats", "adm:bc"} <= set(cbs(msg))
    msg, _ = await w.tap(ADMIN, "adm:u:0")
    assert "adm:usr:42" in cbs(msg)
    msg, _ = await w.tap(ADMIN, "adm:usr:42")
    assert {"adm:tk:42:10", "adm:tk:42:50", "adm:tk:42:100", "adm:tke:42", "adm:acc:42"} <= set(cbs(msg))
    # retirer l'accès -> refusé partout ; le rendre -> de nouveau autorisé
    await w.tap(ADMIN, "adm:acc:42")
    assert not w.app.is_allowed(USER)
    _, q = await w.tap(USER, "menu")
    assert q.answers == ["Accès refusé"]
    await w.tap(ADMIN, "adm:acc:42")
    assert w.app.is_allowed(USER)
    # ajouter un utilisateur par ID (+ jetons) : il est autorisé et prévenu
    await w.tap(ADMIN, "adm:add")
    await w.say(ADMIN, "abc")
    assert "adm_add" in str(w.data[ADMIN].get("await")), "saisie invalide : on reste en attente"
    await w.say(ADMIN, "555 25")
    assert w.app.is_allowed(555) and w.store.tokens(555) == 25
    assert any(m.chat == 555 and "accès" in m.text for m in w.bot.sent)
    # stats globales + diffusion avec aperçu
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
        for t in (test_menus_and_tokens, test_channel_and_friends, test_start_is_clean, test_ssid_in_settings,
                  test_live_trade_messages, test_auto_session_and_result_buttons, test_admin_runs_without_tokens,
                  test_manual_flow, test_admin_panel):
            await t(w)
        await w.app.shutdown()
    print("parcours bot OK")


if __name__ == "__main__":
    asyncio.run(main())
