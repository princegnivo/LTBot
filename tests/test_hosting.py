"""Fiabilité pour l'hébergement : journaux sûrs, contrôle de démarrage, parallélisme, erreurs, redémarrages, performances."""
import asyncio
import logging
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_bot_flow import ADMIN, USER, World, A  # noqa: E402  (installe aussi le faux Telegram si besoin)
import engine as EN  # noqa: E402
import main as MAIN  # noqa: E402
import preflight  # noqa: E402
from config import Config  # noqa: E402
from store import Store, UserSettings  # noqa: E402
from tests import compat  # noqa: E402
from tests.test_engine import ScriptedBroker, mk, boot, fire, sig  # noqa: E402

TOKEN = "123456789:AAFakeTokenFakeTokenFakeTokenFake_x"


def test_logs_never_contain_secrets():
    f = MAIN.SafeFormatter("%(message)s", TOKEN)
    rec = lambda m: logging.LogRecord("x", logging.ERROR, "", 0, m, (), None)
    out = f.format(rec(f"GET https://api.telegram.org/bot{TOKEN}/getUpdates failed"))
    assert TOKEN not in out and "***TOKEN***" in out
    out = f.format(rec('auth {"session":"a:4:{s:10:abcdefghij","isDemo":1} ci_session=abcdef123456; path=/'))
    assert "abcdefghij" not in out and "abcdef123456" not in out
    try:
        raise RuntimeError(f"boom {TOKEN}")
    except RuntimeError:
        import traceback
        r = logging.LogRecord("x", logging.ERROR, "", 0, "err", (), sys.exc_info())
        assert TOKEN not in f.format(r), "tracebacks aussi"
    print("✓ journaux : token / session / cookie masqués (messages et tracebacks)")


def test_preflight():
    with tempfile.TemporaryDirectory() as tmp:
        good = Config(TOKEN, frozenset(), Path(tmp), "paper", False, 20.0, "", "", admin_ids=frozenset({1}),
                      auto_signup=False, po_register_url="https://x.test/r")
        checks = preflight.static_checks(good)
        assert not preflight.has_error([c for c in checks if "BinaryOptions" not in c.text and "telegram" not in c.text])
        assert any("inscriptible" in c.text for c in checks)
        bad = Config("pas-un-token", frozenset(), Path(tmp), "paper", True, 20.0, "", "", admin_ids=frozenset(),
                     deposit_url="https://x.test/dep", selectors_file="/nope.json")
        txt = preflight.report(preflight.static_checks(bad))
        for want in ("TELEGRAM_TOKEN absent ou mal formé", "ADMIN_IDS vide", "{amount}", "PO_SELECTORS_FILE introuvable", "ALLOW_REAL=1"):
            assert want in txt, want
        ro = Config(TOKEN, frozenset(), Path("/proc/forbidden"), "paper", False, 20.0, "", "", admin_ids=frozenset({1}))
        assert any(c.level == "error" and "non inscriptible" in c.text for c in preflight.static_checks(ro))
    print("✓ contrôle de démarrage : token, admins, dossier, URL de dépôt, sélecteurs, ALLOW_REAL")


async def test_users_run_in_parallel_but_each_user_stays_ordered():
    app = types.SimpleNamespace()
    real = A.App(Config("x", frozenset(), Path(tempfile.mkdtemp()), "paper", False, 20.0, "", "", admin_ids=frozenset({1})),
                 Store(tempfile.mkdtemp()))
    ctx = types.SimpleNamespace(application=types.SimpleNamespace(bot_data={"app": real}))
    log = []

    async def slow(update, ctx):
        log.append(("start", update.effective_user.id, update.n))
        await asyncio.sleep(0.4)
        log.append(("end", update.effective_user.id, update.n))
    h = A.serialized(slow)
    mkup = lambda uid, n: types.SimpleNamespace(effective_user=types.SimpleNamespace(id=uid), n=n)
    t0 = time.time()
    await asyncio.gather(h(mkup(1, 1), ctx), h(mkup(2, 1), ctx), h(mkup(1, 2), ctx))
    assert time.time() - t0 < 1.0, "utilisateurs différents : en parallèle"
    u1 = [e for e in log if e[1] == 1]
    assert u1 == [("start", 1, 1), ("end", 1, 1), ("start", 1, 2), ("end", 1, 2)], "même utilisateur : dans l'ordre"
    assert ("start", 2, 1) in log[:3]
    print("✓ parallélisme : un utilisateur lent ne bloque pas les autres ; ordre conservé pour chacun")


async def test_error_handler_never_crashes_and_does_not_spam():
    with tempfile.TemporaryDirectory() as tmp:
        w = World(tmp)
        ctx = types.SimpleNamespace(application=types.SimpleNamespace(bot_data={"app": w.app}), bot=w.bot, error=None)
        chat = types.SimpleNamespace(id=USER)
        upd = types.SimpleNamespace(effective_chat=chat)
        n = len(w.bot.sent)
        for exc in (A._tgerr.NetworkError("down"), A._tgerr.TimedOut("slow")):
            ctx.error = exc
            await A.on_error(upd, ctx)
        assert len(w.bot.sent) == n, "erreurs réseau : silencieuses (nouvelle tentative automatique)"
        for _ in range(3):
            ctx.error = ValueError("bug")
            await A.on_error(upd, ctx)
        admin_msgs = [m for m in w.bot.sent[n:] if m.chat == ADMIN]
        user_msgs = [m for m in w.bot.sent[n:] if m.chat == USER]
        assert len(admin_msgs) == 1 and "ValueError" in admin_msgs[0].text, "admin prévenu 1 fois / 10 min"
        assert len(user_msgs) == 1, "utilisateur prévenu 1 fois / 30 s"
        ctx.error = type("Conflict", (Exception,), {})("2 instances")
        await A.on_error(upd, ctx)                                  # journal clair, aucun envoi, aucune exception
        await A.on_error(None, ctx)
        print("✓ gestionnaire d'erreurs : réseau silencieux, admin/utilisateur prévenus sans spam, conflit d'instance signalé")


async def test_blocked_user_stops_the_session():
    with tempfile.TemporaryDirectory() as tmp:
        w = World(tmp)
        rt = w.app.rt(USER)
        eng, ev, b = mk(tmp, ["win"], stake=1.0)
        await boot(eng)
        rt.engine = eng
        async def forbidden(*a, **k): raise A.Forbidden("bot was blocked by the user")
        res, status = await w.app._call2(rt, forbidden)
        assert status == "gone" and eng.stopping and "bloqué" in eng.stop_reason
        await asyncio.sleep(0.2)
        print("✓ utilisateur qui bloque le bot : sa session est arrêtée (pas de trading sans surveillance)")


async def test_restart_notifies_interrupted_sessions():
    with tempfile.TemporaryDirectory() as tmp:
        w = World(tmp)
        w.store.mark_session(USER, "auto", USER)
        w.store.mark_session(42424, "signals", 42424)
        w.store.mark_session(42424, None)                            # session terminée normalement : pas de message
        assert set(w.store.interrupted_sessions()) == {USER}
        await A._notify_interrupted(w.app)
        msgs = [m for m in w.bot.sent if m.chat == USER]
        assert len(msgs) == 1 and "redémarré" in msgs[0].text and "d'auto-trading" in msgs[0].text
        assert not [m for m in w.bot.sent if m.chat == 42424] and w.store.interrupted_sessions() == {}
        # une session lancée puis terminée proprement n'est pas signalée
        print("✓ redémarrage : l'utilisateur dont la session a été coupée est prévenu (une seule fois)")


async def test_heartbeat_file():
    with tempfile.TemporaryDirectory() as tmp:
        w = World(tmp)
        t = asyncio.create_task(A._heartbeat(w.app))
        await asyncio.sleep(0.3)
        t.cancel()
        hb = Path(tmp) / "heartbeat"
        assert hb.exists() and abs(int(hb.read_text()) - time.time()) < 5
        print("✓ battement de cœur (surveillance Docker / supervision)")


class CandleBroker(ScriptedBroker):
    """Flux de bougies factice : les mêmes données répétées, puis une variation."""
    def __init__(self, n_same=60):
        super().__init__([])
        self.n_same = n_same
        self.step = 0

    async def candles(self, asset, tf):
        closed = [{"time": i * tf, "open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0} for i in range(80)]
        for i in range(self.n_same):
            yield closed, {"time": 80 * tf, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
            await asyncio.sleep(0.002)
        self.step = 1
        for i in range(self.n_same):
            yield closed, {"time": 80 * tf, "open": 1.0, "high": 1.0005, "low": 1.0, "close": 1.0005}
            await asyncio.sleep(0.002)
        self.step = 2


async def test_unchanged_data_is_not_recomputed():
    with tempfile.TemporaryDirectory() as tmp:
        eng, ev, b = mk(tmp, [], stake=1.0, speed="ultra")
        eng.broker = cb = CandleBroker()
        eng.running = True
        task = asyncio.create_task(eng._feed("A_otc", 5))
        for _ in range(100):
            if cb.step >= 1: break
            await asyncio.sleep(0.02)
        assert eng.evals == 1, f"60 ticks identiques -> 1 seule évaluation (obtenu {eng.evals})"
        for _ in range(100):
            if cb.step >= 2: break
            await asyncio.sleep(0.02)
        assert eng.evals == 2, f"une nouvelle valeur -> une évaluation de plus (obtenu {eng.evals})"
        eng._epoch += 1                                              # un trade s'ouvre / se clôt : réévaluation forcée
        eng.running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        print("✓ performance : ticks inchangés = aucun recalcul d'indicateurs")


async def test_stuck_result_releases_the_strategy():
    with tempfile.TemporaryDirectory() as tmp:
        EN.RESULT_GRACE = 0.4
        try:
            eng, ev, b = mk(tmp, ["win"], stake=1.0)
            async def hang(tid, exp, amount): await asyncio.sleep(1000)
            b.result = hang
            await boot(eng)
            d = fire(eng, sig("A_otc", strat="5s"))
            assert d is not None
            await asyncio.sleep(0.2)
            assert eng.open_deals, "trade en attente de résultat"
            await asyncio.sleep(5.6)                                 # expiration 5 s + délai de grâce 0,4 s
            assert not eng.open_deals and d.status == "aborted" and "indisponible" in d.abort_reason
            assert fire(eng, sig("B_otc", strat="5s")) is not None, "la stratégie est libérée"
            await eng.shutdown()
        finally:
            EN.RESULT_GRACE = 45
        print("✓ résultat qui tarde : le trade est abandonné proprement et la stratégie est libérée")


def test_python_310_compatibility():
    root = Path(__file__).resolve().parents[1]
    bad = []
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" not in str(p):
            bad += [(p.name, *b) for b in compat.check(str(p))]
    assert not bad, bad
    print("✓ compatibilité Python 3.10 / 3.11 (aucune f-string réservée à 3.12)")


async def main_async():
    for t in (test_users_run_in_parallel_but_each_user_stays_ordered, test_error_handler_never_crashes_and_does_not_spam,
              test_blocked_user_stops_the_session, test_restart_notifies_interrupted_sessions, test_heartbeat_file,
              test_unchanged_data_is_not_recomputed, test_stuck_result_releases_the_strategy):
        await t()


if __name__ == "__main__":
    test_logs_never_contain_secrets()
    test_preflight()
    test_python_310_compatibility()
    asyncio.run(main_async())
    print("hébergement OK")
