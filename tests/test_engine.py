"""Tests du moteur avec un courtier scripté (aucun réseau)."""
import asyncio
import tempfile
import time

import pandas as pd

from broker import AssetInfo, TradeResult
from engine import Engine, MG_FACTORS
from store import Store, UserSettings
from strategies import REGISTRY
from strategies.signal import Signal

DF = pd.DataFrame({"time": [], "open": [], "high": [], "low": [], "close": []})


class ScriptedBroker:
    def __init__(self, outcomes, balance=1000.0, delay=0.05):
        self.outcomes = list(outcomes)   # "win" | "loss" | "draw" consommés dans l'ordre d'ouverture
        self.bal, self.delay = balance, delay
        self.placed = []                 # (asset, dir, amount)
        self._res = {}

    def is_connected(self): return True
    async def reconnect(self): pass
    async def balance(self): return self.bal
    async def assets(self):
        return [AssetInfo(s, s, 92, "currency", True) for s in ("A_otc", "B_otc", "C_otc", "D_otc")]

    async def place(self, asset, direction, amount, expiration):
        self.placed.append((asset, direction, amount))
        tid = str(len(self.placed))
        self._res[tid] = self.outcomes.pop(0) if self.outcomes else "loss"
        return tid

    async def result(self, tid, expiration, amount):
        await asyncio.sleep(self.delay)
        o = self._res[tid]
        return TradeResult(o, amount * 0.92 if o == "win" else (0.0 if o == "draw" else -amount))


def mk(tmp, outcomes, balance=1000.0, cap=None, **kw):
    base = dict(cooldown_s=0, series_deals=100, take_profit=0, stop_loss=0)
    base.update(kw)
    events = []

    async def notify(kind, **d):
        events.append((kind, d))

    broker = ScriptedBroker(outcomes, balance)
    eng = Engine(1, UserSettings(**base), broker, Store(tmp), notify, stake_cap=cap)
    return eng, events, broker


def sig(asset="A_otc", d="call", strat="1M"):
    return Signal(strat, asset, d, 80, REGISTRY[strat].expiration, "test", 1.0)


async def boot(eng, execute=True, kind=None):
    await eng.start(execute, kind)
    for t in eng._tasks:          # pas de flux réel : on pilote les signaux à la main
        t.cancel()
    for f in eng._feeds.values():
        f.cancel()


def fire(eng, s, key=None):
    return eng.on_signal(s, REGISTRY[s.strategy], DF, time.perf_counter(), key)


def kinds(events): return [k for k, _ in events]


def expected_chain(stake, n):
    out, a = [round(stake, 2)], round(stake, 2)
    for f in MG_FACTORS[: n - 1]:
        a = round(a * f, 2)
        out.append(a)
    return out


def test_no_immediate_double_after_loss():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss"], stake=1.0)
            await boot(eng)
            fire(eng, sig("A_otc"))
            await asyncio.sleep(0.3)
            assert len(b.placed) == 1, "aucune entrée tant qu'une nouvelle confirmation n'est pas arrivée"
            assert eng.mg["1M"] == {"level": 1, "amount": 2.1, "cycle": 1}
            await eng.shutdown()
    asyncio.run(go())


def test_martingale_chain_then_reset_on_win():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "loss", "loss", "win", "loss"], stake=1.0)
            await boot(eng)
            for _ in range(5):
                assert fire(eng, sig("A_otc")) is not None
                await asyncio.sleep(0.2)
            amounts = [a for _, _, a in b.placed]
            assert amounts[:4] == expected_chain(1.0, 4) == [1.0, 2.1, 4.62, 10.63], amounts
            assert amounts[4] == 1.0, "un gain remet la mise de base"
            assert eng.mg["1M"]["level"] == 1 and eng.mg["1M"]["amount"] == 2.1
            await eng.shutdown()
    asyncio.run(go())


def test_chain_exhausted_stops_session():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss"] * 6, stake=1.0, balance=100000.0)
            await boot(eng)
            for _ in range(6):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert [a for _, _, a in b.placed] == expected_chain(1.0, 6)
            await asyncio.sleep(0.2)
            assert not eng.running and "épuisée" in eng.stop_reason
            assert "session_end" in kinds(ev)
            assert fire(eng, sig("A_otc")) is None
    asyncio.run(go())


def test_martingale_disabled_keeps_base_stake():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "loss"], stake=2.0, mg_enabled=False)
            await boot(eng)
            for _ in range(2):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert [a for _, _, a in b.placed] == [2.0, 2.0]
            await eng.shutdown()
    asyncio.run(go())


def test_draw_keeps_martingale_level():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "draw", "win"], stake=1.0)
            await boot(eng)
            for _ in range(3):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert [a for _, _, a in b.placed] == [1.0, 2.1, 2.1]
            assert "1M" not in eng.mg
            await eng.shutdown()
    asyncio.run(go())


def test_stop_loss_blocks_a_step_that_would_exceed_it():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss"] * 3, stake=1.0, stop_loss=5.0, session_mode="tp")
            await boot(eng)
            for _ in range(3):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert [a for _, _, a in b.placed] == [1.0, 2.1], "4,62 ferait passer sous −5 : jamais ouvert"
            await asyncio.sleep(0.2)
            assert not eng.running and "stop-loss" in eng.stop_reason
    asyncio.run(go())


def test_take_profit_stops_session():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["win", "win"], stake=10.0, take_profit=9.0, session_mode="tp")
            await boot(eng)
            fire(eng, sig("A_otc"))
            await asyncio.sleep(0.3)
            assert not eng.running and eng.pnl > 9
            assert fire(eng, sig("B_otc")) is None
    asyncio.run(go())


def test_series_mode_ignores_sl_and_tp():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "loss", "loss", "win"], stake=10.0, stop_loss=5.0, take_profit=1.0,
                            session_mode="series", series_deals=5, balance=100000.0)
            await boot(eng)
            for _ in range(4):
                assert fire(eng, sig("A_otc")) is not None, "ni SL ni TP en mode série"
                await asyncio.sleep(0.2)
            assert eng.pnl < -5 and eng.running or eng.deals_won == 1
            assert "Stop-loss" not in eng.stop_reason and "Take-profit" not in eng.stop_reason
            await eng.shutdown()
    asyncio.run(go())


def test_deals_are_martingale_chains():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "win", "win", "loss", "loss"], stake=1.0)
            await boot(eng)
            for _ in range(5):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert [(d.cycle, d.level) for d in eng.deals] == [(1, 0), (1, 1), (2, 0), (3, 0), (3, 1)]
            assert [d.win_no for d in eng.deals if d.status == "won"] == [1, 2]
            assert eng.winrate == 100.0 and eng.chains_lost == 0
            await eng.shutdown()
    asyncio.run(go())


def test_chain_end_counts_a_lost_deal():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss"] * 6, stake=1.0, balance=100000.0)
            await boot(eng)
            for _ in range(6):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert eng.deals[-1].chain_end and eng.chains_lost == 1 and eng.winrate == 0.0
    asyncio.run(go())


def test_insufficient_balance_stops_session_with_clear_message():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, [], stake=10.0, balance=5.0)
            await boot(eng)
            for _ in range(3):
                assert fire(eng, sig("A_otc")) is None
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.2)
            assert not eng.running and "Solde insuffisant" in eng.stop_reason and "$5.00" in eng.stop_reason
            assert b.placed == []
    asyncio.run(go())


def test_skip_log_is_rate_limited():
    import logging
    class H(logging.Handler):
        def __init__(self): super().__init__(); self.n = 0
        def emit(self, r): self.n += 1
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["win"], stake=1.0)
            await boot(eng)
            h = H(); logging.getLogger("engine").addHandler(h); logging.getLogger("engine").setLevel(logging.INFO)
            fire(eng, sig("A_otc"))
            for _ in range(50):
                fire(eng, sig("B_otc"))             # « trade déjà en cours sur cette stratégie » ×50
            assert h.n <= 2, h.n
            await asyncio.sleep(0.3)
    asyncio.run(go())


def test_one_open_trade_per_strategy():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["win", "win", "win"], stake=1.0)
            await boot(eng)
            assert fire(eng, sig("A_otc", strat="1M")) is not None
            assert fire(eng, sig("B_otc", strat="1M")) is None, "la mise dépend du résultat précédent"
            assert fire(eng, sig("B_otc", strat="2M")) is not None, "une autre stratégie est indépendante"
            await asyncio.sleep(0.3)
            await eng.shutdown()
    asyncio.run(go())


def test_martingale_is_per_strategy():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "win"], stake=1.0)
            await boot(eng)
            fire(eng, sig("A_otc", strat="1M"))
            await asyncio.sleep(0.2)
            fire(eng, sig("B_otc", strat="5M"))
            await asyncio.sleep(0.2)
            assert [a for _, _, a in b.placed] == [1.0, 1.0], "la perte de 1M n'agit pas sur 5M"
            assert eng.mg["1M"]["level"] == 1 and "5M" not in eng.mg
            await eng.shutdown()
    asyncio.run(go())


def test_real_cap_interrupts_martingale():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "loss"], stake=5.0, cap=10.0)
            await boot(eng)
            fire(eng, sig("A_otc"))
            await asyncio.sleep(0.2)
            assert fire(eng, sig("A_otc")) is None        # 5 × 2,1 = 10,5 > 10
            await asyncio.sleep(0.2)
            assert not eng.running and "plafond" in eng.stop_reason
    asyncio.run(go())


def test_every_trade_is_counted():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["loss", "win", "draw"], stake=1.0)
            await boot(eng)
            for _ in range(3):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert len(eng.deals) == 3 and (eng.deals_won, eng.deals_lost, eng.deals_draw) == (1, 1, 1)
            assert len(Store(tmp).trades(1)) == 3
            await eng.shutdown()
    asyncio.run(go())


def test_signals_only_never_trades():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, [], stake=1.0)
            await boot(eng, execute=False)
            assert fire(eng, sig("A_otc"), key=("A_otc", "1M", 1)) is None
            assert fire(eng, sig("A_otc"), key=("A_otc", "1M", 1)) is None      # même bougie : un seul message
            await asyncio.sleep(0.1)
            assert b.placed == [] and kinds(ev).count("signal") == 1
            await eng.shutdown()
    asyncio.run(go())


def test_blocked_signal_is_retried_without_notification():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["win", "win"], stake=1.0)
            await boot(eng)
            key = ("B_otc", "1M", 7)
            assert fire(eng, sig("A_otc")) is not None
            assert fire(eng, sig("B_otc"), key) is None            # bloqué : un trade 1M est en cours
            assert eng._fired.get(("B_otc", "1M")) is None, "pas marqué comme traité : sera retenté"
            await asyncio.sleep(0.3)
            assert fire(eng, sig("B_otc"), key) is not None        # retenté une fois libre
            await asyncio.sleep(0.3)
            assert kinds(ev).count("signal") == 2
            await eng.shutdown()
    asyncio.run(go())


def test_manual_signal_then_place():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, ["win"], stake=1.0)
            await boot(eng, execute=False, kind="manual")
            assert fire(eng, sig("A_otc", strat="5s"), key=("A_otc", "5s", 1)) is None
            await asyncio.sleep(0.05)
            sid = [d["sid"] for k, d in ev if k == "signal"][0]
            assert b.placed == [], "rien n'est placé sans le bouton"
            deal = await eng.place_signal(sid, 3.0)
            assert deal.ref == sid and deal.amount == 3.0
            try:
                await eng.place_signal(sid, 3.0)
                raise AssertionError("double placement accepté")
            except ValueError:
                pass
            await asyncio.sleep(0.3)
            assert b.placed == [("A_otc", "call", 3.0)] and eng.deals_won == 1
            assert "position_closed" in kinds(ev)
            await eng.shutdown()
    asyncio.run(go())


def test_manual_signal_expires():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev, b = mk(tmp, [], stake=1.0)
            await boot(eng, execute=False, kind="manual")
            fire(eng, sig("A_otc", strat="5s"), key=("A_otc", "5s", 1))
            sid = next(iter(eng.sigs))
            s_, info, ts = eng.sigs[sid]
            eng.sigs[sid] = (s_, info, ts - info.max_delay - 1)
            try:
                await eng.place_signal(sid, 1.0)
                raise AssertionError("signal périmé accepté")
            except ValueError as e:
                assert "expiré" in str(e)
            await eng.shutdown()
    asyncio.run(go())


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("✓", n)
