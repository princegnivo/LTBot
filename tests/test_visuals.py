"""Images : bannières, tirage aléatoire (IMG1/IMG2), cartes de résultat, plancher 92 %, partage admin."""
import asyncio
import io
import tempfile
import time
import types
from pathlib import Path

from PIL import Image

import tests.test_bot_flow  # noqa: F401  (installe un faux module « telegram » quand la vraie bibliothèque est absente)
import sys

if not hasattr(sys.modules["telegram"], "InputMediaPhoto"):
    class _InputMediaPhoto:
        def __init__(self, media, caption=None, parse_mode=None):
            self.media, self.caption = media, caption
    sys.modules["telegram"].InputMediaPhoto = _InputMediaPhoto

import visuals  # noqa: E402
from bot import accounts as AC  # noqa: E402
from bot.ui import show  # noqa: E402
from store import PAYOUT_FLOOR, Store  # noqa: E402


def make_assets(root: Path, sizes=((1364, 768), (800, 450))) -> None:
    for folder in ("IMG1", "IMG2", "share"):
        (root / folder).mkdir(parents=True)
    for i, size in enumerate(sizes):
        Image.new("RGB", size, (20, 30 + i * 10, 25)).save(root / "IMG1" / f"a{i}.JPG")
        Image.new("RGB", size, (30, 40 + i * 10, 35)).save(root / "IMG2" / f"b{i}.jpg")


def test_pick_is_random_and_tolerant():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        visuals.configure(str(root))
        assert visuals.pick("IMG1") is None, "dossier absent : pas d'erreur"
        make_assets(root)
        seen = {visuals.pick("IMG1").name for _ in range(12)}
        assert seen == {"a0.JPG", "a1.JPG"}, "extensions .JPG acceptées, les deux images sortent"
        a = visuals.pick("IMG2")
        assert visuals.pick("IMG2") != a, "jamais deux fois de suite la même image"
        assert visuals.pick("share") is None
    visuals.configure("")


def test_cards_keep_background_size():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        visuals.configure(str(root))
        make_assets(root)
        for p in visuals.images_in("IMG2"):
            w, h = Image.open(p).size
            img = Image.open(io.BytesIO(visuals.render_result(p, "@bot", "AUD/CHF OTC", 5.22, 1.0, "2m 14s", "1M", "5/5")))
            assert img.size == (w, h), "même longueur et largeur que le fond vierge"
            img = Image.open(io.BytesIO(visuals.render_total(p, "@bot", 190735.0)))
            assert img.size == (w, h)
        corrupt = root / "IMG2" / "bad.jpg"
        corrupt.write_bytes(b"pas une image")
        assert visuals.render_result(corrupt, "@bot", "X", 1, 1, "1m", "5M", "1/6")[:2] == b"\xff\xd8", "fond illisible : repli, pas d'erreur"
    visuals.configure("")


def test_banners_shipped_with_original_sizes():
    visuals.configure("")
    for name in ("menu", "welcome", "tokens", "bonus", "friends", "deposit", "settings", "community"):
        p = visuals.banner_path(name)
        assert p is not None and Image.open(p).size == (800, 384), f"{name} : toutes les bannières au même format"


def test_payout_floor_is_enforced():
    with tempfile.TemporaryDirectory() as d:
        st = Store(Path(d))
        s = st.settings(1)
        s.min_payout = 85
        st.save_settings(1, s)
        assert st.settings(1).min_payout == PAYOUT_FLOOR == 92, "un ancien réglage (85/90) est relevé à 92"


class FakeMsg:
    def __init__(self, photo=False):
        self.photo = [types.SimpleNamespace(file_id="F1")] if photo else []
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChat:
    id = 7

    def __init__(self):
        self.sent = []

    async def send_photo(self, arg, caption=None, parse_mode=None, reply_markup=None):
        m = FakeMsg(True)
        m.caption = caption
        self.sent.append(("photo", m))
        return m

    async def send_message(self, text, parse_mode=None, reply_markup=None):
        m = FakeMsg()
        m.text = text
        self.sent.append(("text", m))
        return m


class FakeQuery:
    def __init__(self, message):
        self.message = message
        self.edits = []

    async def edit_message_text(self, text, **k):
        self.edits.append(("text", text))

    async def edit_message_media(self, media, reply_markup=None):
        self.edits.append(("media", media.caption))
        return self.message


def test_show_switches_between_photo_and_text():
    async def run():
        visuals.configure("")
        visuals.FILE_IDS.clear()
        chat = FakeChat()
        # 1) commande : bannière -> photo avec légende
        await show(types.SimpleNamespace(callback_query=None, effective_chat=chat), "Menu", [], banner="menu")
        assert chat.sent[-1][0] == "photo" and chat.sent[-1][1].caption == "Menu"
        # 2) bouton depuis une photo vers un écran SANS bannière : nouveau texte + ancienne photo supprimée
        old = chat.sent[-1][1]
        up = types.SimpleNamespace(callback_query=FakeQuery(old), effective_chat=chat)
        await show(up, "Autre écran", [])
        assert chat.sent[-1][0] == "text" and old.deleted
        # 3) bouton depuis un texte vers un écran avec bannière : photo + ancien texte supprimé
        txt = chat.sent[-1][1]
        await show(types.SimpleNamespace(callback_query=FakeQuery(txt), effective_chat=chat), "Jetons", [], banner="tokens")
        assert chat.sent[-1][0] == "photo" and txt.deleted
        # 4) photo -> photo : édition sur place, rien de nouveau
        n = len(chat.sent)
        q = FakeQuery(chat.sent[-1][1])
        await show(types.SimpleNamespace(callback_query=q, effective_chat=chat), "Bonus", [], banner="bonus")
        assert q.edits == [("media", "Bonus")] and len(chat.sent) == n
        # 5) texte trop long pour une légende : écran texte (jamais d'erreur)
        await show(types.SimpleNamespace(callback_query=None, effective_chat=chat), "x" * 1500, [], banner="menu")
        assert chat.sent[-1][0] == "text"
    asyncio.run(run())


def test_share_uses_real_net_total():
    with tempfile.TemporaryDirectory() as d:
        st = Store(Path(d))
        st.log_trade(1, dict(strategy="1M", asset="A", outcome="win", profit=30.0, demo=True))
        st.log_trade(2, dict(strategy="1M", asset="A", outcome="loss", profit=-12.5, demo=False))
        st.log_trade(2, dict(strategy="2M", asset="B", outcome="win", profit=2.5, demo=False))
        app = types.SimpleNamespace(store=st, bot_username="LegitTradeAI_bot")
        t = AC.share_totals(app, "week")
        assert t["net"] == 20.0 and t["n"] == 3 and t["users"] == 2, "net de TOUS les utilisateurs, pertes comprises"
        assert t["demo"] == 30.0 and t["real"] == -10.0
        cap = AC.share_caption(app, "week", t)
        assert cap.startswith("📊 Ces 7 derniers jours, les utilisateurs ont déjà tradé <b>+$20</b> !")
        assert "Teste en démo — appuie sur le bouton 🚀" in cap and "risque de perte" in cap
        btn = AC.start_button(app)[0]
        assert "Commencer" in btn.text
        assert btn.url == "https://t.me/LegitTradeAI_bot?start=share"


def test_bot_sends_signal_with_image_on_top_and_edits_caption():
    from tests.test_bot_flow import FakeBot, FakeMsg, USER, World
    from tests.test_views import mkd

    class PhotoMsg(FakeMsg):
        def __init__(self, bot, chat, caption, markup, mid):
            super().__init__(bot, chat, caption, markup, mid)
            self.photo = [types.SimpleNamespace(file_id=f"id{mid}")]

        async def edit_caption(self, caption=None, **kw):
            assert not self.deleted
            self.text, self.markup, self.edits = caption, kw.get("reply_markup"), self.edits + 1

    class PhotoBot(FakeBot):
        async def send_photo(self, chat_id, photo, **kw):
            self.n += 1
            m = PhotoMsg(self, chat_id, kw.get("caption") or "", kw.get("reply_markup"), self.n)
            self.sent.append(m)
            self.photos.append((chat_id, type(photo).__name__, kw))
            return m

    async def run():
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as assets:
            visuals.configure(assets)
            visuals.FILE_IDS.clear()
            make_assets(Path(assets))
            w = World(tmp)
            w.bot = w.app.bot = PhotoBot()
            rt = w.app.rt(USER)
            rt.chat_id = USER
            # 1er signal : UN seul message = image IMG1 au-dessus, texte du signal en légende
            await w.app._on_signal(rt, None, {**mkd(), "sid": None, "deal": None})
            first = w.bot.sent[:]
            assert [type(m).__name__ for m in first] == ["PhotoMsg"] and first[0].text, "image + texte dans le même message"
            # le texte du signal reste modifiable (animation, résultat) : la légende est éditée
            assert await w.app.edit(rt, first[0], "nouveau texte") == "ok" and first[0].text == "nouveau texte"
            # 2e signal : le précédent est supprimé (image comprise) avant l'envoi du nouveau
            await w.app._on_signal(rt, None, {**mkd(), "sid": None, "deal": None})
            assert first[0].deleted, "signal précédent supprimé avec son image"
            await w.app._on_signal(rt, None, {**mkd(), "sid": None, "deal": None})
            assert "str" in {p[1] for p in w.bot.photos}, "image déjà envoyée : file_id réutilisé, pas de renvoi du fichier"
            # sans image dans IMG1 : signal en texte, aucune erreur
            for f in (Path(assets) / "IMG1").iterdir():
                f.unlink()
            n = len(w.bot.sent)
            await w.app._on_signal(rt, None, {**mkd(), "sid": None, "deal": None})
            assert len(w.bot.sent) == n + 1 and type(w.bot.sent[-1]).__name__ == "FakeMsg"
            # carte de résultat : session gagnante seulement, fond IMG2
            w.store.log_trade(USER, dict(strategy="1M", asset="AUDCHF_otc", step=1, amount=1.0, outcome="win", profit=5.22, demo=True))
            t0 = time.time() - 134
            names = {"AUDCHF_otc": "AUD/CHF OTC"}
            auto = types.SimpleNamespace(asset_mode="auto", asset="", market="otc", stake=1.0)
            eng = types.SimpleNamespace(kind="auto", pnl=5.22, started_at=t0, names=names, cfg=auto, deals_won=5, deals=[0] * 5)
            card = await w.app.result_card(rt, eng)
            assert card and Image.open(io.BytesIO(card)).size in {(1364, 768), (800, 450)}
            eng.pnl = -3.0
            assert await w.app.result_card(rt, eng) is None, "pas de carte pour une session perdante"
            msg = await w.app.send_photo(rt, card, "Résultat", [])
            assert msg.text == "Résultat" and w.bot.photos[-1][2]["caption"] == "Résultat"
        visuals.configure("")
    asyncio.run(run())


def test_card_title_random_or_chosen_pair_and_handle():
    from tests.test_bot_flow import USER, World
    seen = []
    real = visuals.render_result

    def spy(bg, handle, pair, *a):
        seen.append((handle, pair, a[-1]))
        return real(bg, handle, pair, *a)

    async def run():
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as assets:
            visuals.configure(assets)
            make_assets(Path(assets))
            visuals.render_result = spy
            w = World(tmp)
            w.app.bot_username = "SuperTradingAIbot"                       # bot de test : l'image affiche quand même le pseudo voulu
            rt = w.app.rt(USER)
            w.store.log_trade(USER, dict(strategy="1M", asset="AUDCHF_otc", step=1, amount=1.0, outcome="win", profit=5.22, demo=True))
            names = {"AUDCHF_otc": "AUD/CHF OTC"}
            mk = lambda **c: types.SimpleNamespace(kind="auto", pnl=5.22, started_at=time.time() - 60, names=names, deals_won=5,
                                                   deals=[0] * 5, cfg=types.SimpleNamespace(stake=1.0, **c))
            await w.app.result_card(rt, mk(asset_mode="auto", asset="", market="otc"))
            await w.app.result_card(rt, mk(asset_mode="auto", asset="", market="real"))
            await w.app.result_card(rt, mk(asset_mode="manual", asset="AUDCHF_otc", market="otc"))
        visuals.render_result = real
        visuals.configure("")
    asyncio.run(run())
    assert [x[0] for x in seen] == ["@LegitTradeAI_bot"] * 3, seen
    assert [x[1] for x in seen] == ["Aléatoire OTC", "Aléatoire", "AUD/CHF OTC"], seen
    assert all(x[2] == "5/5" for x in seen)


def test_admin_gets_extra_keyboard_button():
    from bot.app import KEY_ROUTES, REPLY_KB, REPLY_KB_ADMIN
    def flat(kb):                                   # vrai ReplyKeyboardMarkup (.keyboard) ou faux des tests (.rows)
        rows = getattr(kb, "keyboard", None) or kb.rows
        return [b if isinstance(b, str) else b.text for row in rows for b in row]
    assert "🛠 Admin" in flat(REPLY_KB_ADMIN) and "🛠 Admin" not in flat(REPLY_KB), "bouton Admin réservé à l'admin"
    assert set(flat(REPLY_KB)) <= set(flat(REPLY_KB_ADMIN)) and KEY_ROUTES["🛠 Admin"] == ["adm"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
    print("visuels OK")
