"""Parcours complet des menus avec un faux Telegram (stub) + simulateur de courtier. Aucun réseau."""
import asyncio
import sys
import tempfile
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
        def __init__(self, text, callback_data=None): self.text, self.callback_data = text, callback_data
    class Markup:
        def __init__(self, rows): self.rows = rows
    tg.InlineKeyboardButton, tg.InlineKeyboardMarkup, tg.Update = Btn, Markup, object
    class Any:
        def __init__(self, *a, **k): pass
        def __getattr__(self, n): return lambda *a, **k: self
    class Filters:
        TEXT = COMMAND = Any()
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
from config import Config  # noqa: E402
from store import Store  # noqa: E402


class FakeBot:
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text, **kw):
        m = FakeMsg(self, text, kw.get("reply_markup")); self.sent.append(m); return m


class FakeMsg:
    def __init__(self, bot, text, markup): self.bot, self.text, self.markup = bot, text, markup
    async def edit_text(self, text, **kw): self.text, self.markup = text, kw.get("reply_markup")
    async def reply_text(self, text, **kw): return await self.bot.send_message(1, text, **kw)


class FakeQ:
    def __init__(self, data, msg): self.data, self.message, self.msg = data, msg, msg
    async def answer(self, *a, **k): pass
    async def edit_message_text(self, text, **kw): self.msg.text, self.msg.markup = text, kw.get("reply_markup")


class FakeUpdate:
    def __init__(self, bot, data, msg):
        self.callback_query = FakeQ(data, msg)
        self.effective_user = types.SimpleNamespace(id=42)
        self.effective_chat = types.SimpleNamespace(id=1, send_message=lambda t, **k: bot.send_message(1, t, **k))
        self.effective_message = msg


def buttons(msg): return [b.callback_data for row in msg.markup.rows for b in row]


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config("x", frozenset({42}), Path(tmp), "paper", False, 20.0, "", "")
        store = Store(tmp)
        app = A.App(cfg, store); bot = FakeBot(); app.bot = bot
        ctx = types.SimpleNamespace(application=types.SimpleNamespace(bot_data={"app": app}), user_data={})
        panel = FakeMsg(bot, "", None)

        async def tap(data):
            await A.on_callback(FakeUpdate(bot, data, panel), ctx)
            return panel

        m = await tap("menu");           assert "auto" in buttons(m) and "sig" in buttons(m) and "man" in buttons(m)
        for d in ("help", "set", "set:g:sig", "set:g:ast", "set:g:stk", "set:g:rsk", "set:g:ntf", "set:st", "acc", "acc:stats:today"):
            m = await tap(d); assert m.text, d
        m = await tap("set:sp");          assert "set:sv:cloture" in buttons(m) and "set:sv:ultra" in buttons(m)
        m = await tap("set:sv:cloture");  assert app.settings(42).speed == "cloture"
        m = await tap("set:sv:ultra");    assert app.settings(42).speed == "ultra"
        m = await tap("set:g:sig");       assert "set:sp" in buttons(m)
        m = await tap("set:s:2M");        assert "❌" in "".join(b.text for r in m.markup.rows for b in r)
        m = await tap("set:s:2M")
        m = await tap("set:k:take_profit"); assert "set:e:take_profit" in buttons(m)
        m = await tap("set:v:take_profit:50"); assert app.settings(42).take_profit == 50.0
        try:
            A.apply_value(app, 42, app.settings(42), "stake", "0"); assert False
        except ValueError: pass

        # mode réel verrouillé
        m = await tap("mode"); assert "verrouillé" in m.text

        # manuel : marché -> catégorie -> actif -> analyse -> ordre
        m = await tap("man");          assert "man:mk:otc" in buttons(m)
        m = await tap("man:mk:otc");   assert any(b.startswith("man:ct:otc:") for b in buttons(m))
        m = await tap("man:ct:otc:currency"); picks = [b for b in buttons(m) if b.startswith("man:pk:")]; assert picks, buttons(m)
        m = await tap(picks[0]);       assert "Choisissez le sens" in m.text and "man:go:call" in buttons(m), m.text
        s = app.settings(42); s.manual_exp = 5; s.stake = 2.0; store.save_settings(42, s)
        n0 = len(bot.sent)
        await tap("man:go:call")
        await asyncio.sleep(0.5)
        assert any("Ordre passé" in x.text for x in bot.sent[n0:]), [x.text for x in bot.sent[n0:]]
        await asyncio.sleep(6)
        assert any(("Gagné" in x.text or "Perdu" in x.text or "Égalité" in x.text) for x in bot.sent[n0:])

        # signaux seuls (auto) puis arrêt
        m = await tap("sig");          assert "sig:as:auto" in buttons(m)
        await tap("sig:as:auto")
        rt = app.rt(42); assert rt.engine and rt.engine.running and rt.engine.kind == "signals"
        await asyncio.sleep(2.5)
        await tap("run:stop"); await asyncio.sleep(3)
        assert not rt.engine.running

        # auto-trading complet
        m = await tap("auto");          assert "auto:stake:5" in buttons(m)
        m = await tap("auto:stake:1");  assert "auto:as:auto" in buttons(m)
        m = await tap("auto:as:auto");  assert "auto:md:series" in buttons(m)
        m = await tap("auto:md:series")
        rt = app.rt(42); assert rt.engine.kind == "auto" and rt.engine.running
        dash = [x for x in bot.sent if "Auto-trading" in x.text]; assert dash
        await asyncio.sleep(3); rt.engine.stop("test")
        assert rt.engine.stopping
        for _ in range(140):                       # les positions ouvertes vont à terme (≤ 120 s en simulateur)
            if not rt.engine.running:
                break
            await asyncio.sleep(1)
        assert not rt.engine.running and not rt.engine.open_deals
        await asyncio.sleep(2.5)
        assert any("Résultat" in x.text for x in bot.sent) and "Auto-trading" in dash[-1].text
        await app.shutdown()
        print("parcours menus OK · messages envoyés:", len(bot.sent))

asyncio.run(main())
