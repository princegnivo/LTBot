"""Tests du moteur avec un courtier scripté (aucun réseau)."""
import asyncio
import tempfile
import time

import pandas as pd

from broker import AssetInfo, TradeResult
from engine import Engine
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


def mk(tmp, outcomes, balance=1000.0, **kw):
    cfg = UserSettings(cooldown_s=0, **kw)
    events = []

    async def notify(kind, **d):
        events.append(kind)

    eng = Engine(1, cfg, ScriptedBroker(outcomes, balance), Store(tmp), notify)
    return eng, events


def sig(asset, d="call", strat="1M"):
    return Signal(strat, asset, d, 80, 60, "test", 1.0)


async def boot(eng, execute=True):
    await eng.start(execute)
    for t in eng._tasks:          # pas de flux réel : on pilote les signaux à la main
        t.cancel()
    for f in eng._feeds.values():
        f.cancel()


def fire(eng, s, strat=None):
    return eng.on_signal(s, REGISTRY[strat or s.strategy], DF, time.perf_counter())


def test_martingale_recovery_amounts():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, _ = mk(tmp, ["loss", "win"], stake=10.0, series_deals=1, take_profit=0, stop_loss=0)
            await boot(eng)
            fire(eng, sig("A_otc", strat="5s"))
            await asyncio.sleep(0.4)
            amts = [a for _, _, a in eng.broker.placed]
            assert amts[0] == 10.0 and amts[1] == 20.87, amts       # (10 + 9.2) / 0.92
            assert abs(eng.pnl - 9.2) < 0.02, eng.pnl               # gain net = gain visé
            assert eng.deals_won == 1 and eng.deals_lost == 0
    asyncio.run(go())


def test_multiple_positions_and_caps():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, _ = mk(tmp, ["win"] * 10, stake=10.0, max_open=3, series_deals=20, take_profit=0, stop_loss=0)
            eng.broker.delay = 0.3
            await boot(eng)
            assert fire(eng, sig("A_otc")) and fire(eng, sig("B_otc")) and fire(eng, sig("C_otc"))
            assert fire(eng, sig("D_otc")) is None                  # 4e refusé : max_open=3
            assert len(eng.open_deals) == 3
            await asyncio.sleep(0.6)
            assert fire(eng, sig("A_otc")) is not None
            assert fire(eng, sig("A_otc", "put", "2M")) is None     # déjà une position sur cet actif
    asyncio.run(go())


def test_take_profit_stops_session():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev = mk(tmp, ["win"] * 10, stake=10.0, session_mode="tp", take_profit=15.0, max_open=1)
            await boot(eng)
            for _ in range(2):
                fire(eng, sig("A_otc"))
                await asyncio.sleep(0.2)
            assert eng.stopping and "Take-profit" in eng.stop_reason
            n = len(eng.broker.placed)
            assert fire(eng, sig("B_otc")) is None                  # plus rien après le seuil
            await asyncio.sleep(0.2)
            assert len(eng.broker.placed) == n and "session_end" in ev
    asyncio.run(go())


def test_stop_loss_blocks_next_martingale_step():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev = mk(tmp, ["loss"] * 10, stake=10.0, session_mode="tp", stop_loss=15.0, mg_steps=2)
            await boot(eng)
            fire(eng, sig("A_otc", strat="5s"))
            await asyncio.sleep(0.6)
            assert len(eng.broker.placed) == 2, eng.broker.placed   # perte 10 + 20.87 => SL franchi, pas d'étape 3
            assert eng.stopping and "Stop-loss" in eng.stop_reason and "session_end" in ev
    asyncio.run(go())


def test_series_finishes_after_n_deals():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev = mk(tmp, ["win"] * 5, stake=5.0, series_deals=2, max_open=1, take_profit=0, stop_loss=0)
            await boot(eng)
            fire(eng, sig("A_otc")); await asyncio.sleep(0.2)
            fire(eng, sig("B_otc")); await asyncio.sleep(0.3)
            assert eng.deals_started == 2 and "session_end" in ev
            assert fire(eng, sig("C_otc")) is None
    asyncio.run(go())


def test_step_risk_threshold_aborts_chain():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, _ = mk(tmp, ["loss"] * 5, balance=100.0, stake=10.0, mg_steps=3, max_step_pct=25.0,
                        session_mode="tp", take_profit=0, stop_loss=0, max_consec_losses=0)
            await boot(eng)
            fire(eng, sig("A_otc", strat="5s"))
            await asyncio.sleep(0.5)
            amts = [a for _, _, a in eng.broker.placed]
            assert amts == [10.0, 20.87], amts       # étape 3 = 43.6 $ > 25 % du solde -> refusée
            assert eng.deals[0].status == "aborted"
    asyncio.run(go())


def test_2m_has_no_martingale_and_consec_loss_stop():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, _ = mk(tmp, ["loss"] * 6, stake=5.0, session_mode="tp", take_profit=0, stop_loss=0,
                        max_consec_losses=2, max_open=1)
            await boot(eng)
            for a in ("A_otc", "B_otc", "C_otc"):
                fire(eng, sig(a, strat="2M"), "2M")
                await asyncio.sleep(0.2)
            assert len(eng.broker.placed) == 2                     # 2M : 1 mise par deal, arrêt après 2 pertes
            assert "d'affilée" in eng.stop_reason
    asyncio.run(go())


def test_martingale_only_on_5s():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            for strat, expected in (("1M", 1), ("2M", 1), ("5s", 3)):
                eng, _ = mk(tmp, ["loss"] * 5, stake=1.0, mg_steps=2, session_mode="tp",
                            take_profit=0, stop_loss=0, max_consec_losses=0)
                await boot(eng)
                fire(eng, sig("A_otc", strat=strat))
                await asyncio.sleep(0.5)
                assert len(eng.broker.placed) == expected, (strat, eng.broker.placed)
    asyncio.run(go())


def test_auto_scan_only_92_currencies():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, _ = mk(tmp, [])
            eng.assets = [AssetInfo("EURUSD_otc", "EUR/USD OTC", 92, "currency", True),
                          AssetInfo("GBPUSD_otc", "GBP/USD OTC", 85, "currency", True),
                          AssetInfo("AXP_otc", "Amex OTC", 92, "other", True),
                          AssetInfo("BTC_otc", "Bitcoin OTC", 92, "crypto", True),
                          AssetInfo("EURCHF_otc", "EUR/CHF OTC", 92, "currency", True)]
            assert sorted(eng.pick_assets()) == ["EURCHF_otc", "EURUSD_otc"], eng.pick_assets()
    asyncio.run(go())


def test_watchdog_reconnects_once_and_stops_when_dead():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev = mk(tmp, [], stake=1.0)
            eng.WATCHDOG_S = 0.05
            b = eng.broker
            state = {"up": False, "tries": 0}
            b.is_connected = lambda: state["up"]
            async def rec():
                state["tries"] += 1
                if state["tries"] >= 3:
                    state["up"] = True
            b.reconnect = rec
            infos = []
            async def notify(kind, **d):
                ev.append(kind)
                if kind == "info": infos.append(d["text"])
            eng._notify = notify
            await eng.prepare(); eng.running = True
            t = asyncio.create_task(eng._watchdog())
            await asyncio.sleep(0.6); eng.running = False; t.cancel()
            assert state["tries"] == 3 and sum("perdue" in x for x in infos) == 1 and any("rétablie" in x for x in infos), infos
            # SSID mort : la reconnexion échoue toujours -> arrêt propre
            eng2, _ = mk(tmp, [], stake=1.0)
            eng2.WATCHDOG_S = 0.02
            eng2.broker.is_connected = lambda: False
            async def bad(): raise RuntimeError("ssid mort")
            eng2.broker.reconnect = bad
            await eng2.prepare(); eng2.running = True
            t2 = asyncio.create_task(eng2._watchdog())
            await asyncio.sleep(0.5); t2.cancel()
            assert eng2.stopping and "Reconnexion impossible" in eng2.stop_reason
    asyncio.run(go())


def test_signals_only_never_trades():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, ev = mk(tmp, ["win"] * 3)
            await boot(eng, execute=False)
            assert fire(eng, sig("A_otc")) is None
            await asyncio.sleep(0.1)
            assert eng.broker.placed == [] and "signal" in ev
    asyncio.run(go())


def test_manual_trade_and_real_cap():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            eng, _ = mk(tmp, ["win"])
            eng.stake_cap = 5.0
            await eng.prepare()
            try:
                await eng.manual_trade("A_otc", "call", 10.0, 60)
                assert False
            except ValueError as e:
                assert "plafond" in str(e)
            d = await eng.manual_trade("A_otc", "put", 5.0, 60)
            await asyncio.sleep(0.2)
            assert d.status == "won" and eng.broker.placed[0][1] == "put"
    asyncio.run(go())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
