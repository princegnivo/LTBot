"""Couche courtier : BinaryOptionsToolsV2 (PocketOptionAsync) + simulateur local (paper)."""
import asyncio
import json
import math
import random
import time
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional, Tuple


@dataclass
class AssetInfo:
    symbol: str
    name: str
    payout: int
    category: str      # "currency" | "crypto" | "other"
    is_otc: bool
    active: bool = True


@dataclass
class TradeResult:
    outcome: str       # "win" | "loss" | "draw"
    profit: float      # gain net (négatif si perte)


def _category(asset_type: str) -> str:
    t = (asset_type or "").lower()
    if "crypto" in t:
        return "crypto"
    if "currency" in t or "forex" in t:
        return "currency"
    return "other"


class PocketBroker:
    """Adaptateur autour de BinaryOptionsToolsV2.pocketoption.asynchronous.PocketOptionAsync."""

    def __init__(self, ssid: str):
        self.ssid = ssid
        self.client = None

    async def start(self) -> None:
        from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync
        self.client = PocketOptionAsync(self.ssid)
        await self.client.wait_for_assets(timeout=60.0)

    async def stop(self) -> None:
        if self.client is not None:
            try:
                await self.client.shutdown()
            except Exception:
                pass
            self.client = None

    def is_connected(self) -> bool:
        try:
            return bool(self.client is not None and self.client.is_connected())
        except Exception:
            return False

    async def reconnect(self) -> None:
        """Tente une reconnexion douce ; si le canal est mort ("half closed channel"), recrée le client."""
        try:
            if self.client is not None:
                await asyncio.wait_for(self.client.reconnect(), 30)
                await asyncio.sleep(2)
                if self.is_connected():
                    return
        except Exception:
            pass
        await self._rebuild()

    async def _rebuild(self) -> None:
        from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync
        old, self.client = self.client, None
        if old is not None:
            try:
                await asyncio.wait_for(old.shutdown(), 10)
            except Exception:
                pass
        client = PocketOptionAsync(self.ssid)
        await client.wait_for_assets(timeout=60.0)
        self.client = client

    def is_demo(self) -> bool:
        return bool(self.client.is_demo())

    async def balance(self) -> float:
        return float(await self.client.balance())

    async def assets(self) -> List[AssetInfo]:
        out = []
        for a in await self.client.active_assets():
            sym = a.get("symbol") or ""
            if not sym:
                continue
            out.append(AssetInfo(
                symbol=sym,
                name=a.get("name") or sym,
                payout=int(a.get("payout") or 0),
                category=_category(a.get("asset_type", "")),
                is_otc=bool(a.get("is_otc", sym.endswith("_otc"))),
                active=bool(a.get("is_active", True)),
            ))
        return out

    async def place(self, asset: str, direction: str, amount: float, expiration: int) -> str:
        fn = self.client.buy if direction == "call" else self.client.sell
        trade_id, _ = await fn(asset, float(amount), int(expiration), check_win=False)
        return str(trade_id)

    async def result(self, trade_id: str, expiration: int, amount: float) -> TradeResult:
        res, err = None, None
        for attempt in range(3):                       # une coupure pendant l'expiration ne doit pas perdre le résultat
            try:
                res = await self.client.check_win(trade_id, timeout_seconds=expiration + 45)
                break
            except Exception as e:
                err = e
                await asyncio.sleep(4)
                if not self.is_connected():
                    try:
                        await self.reconnect()
                    except Exception:
                        pass
        if res is None:
            raise err
        outcome = res.get("result", "loss")
        profit = float(res.get("profit", 0.0))
        if outcome == "loss":
            profit = -abs(profit) if profit else -float(amount)
        elif outcome == "draw":
            profit = 0.0
        return TradeResult(outcome, profit)

    def candles(self, asset: str, period: int, rows: int = 80) -> AsyncIterator[Tuple[List[dict], Optional[dict]]]:
        hours = max(0.1, period * rows / 3600.0)
        return self.client.get_candles_live(asset, period, hours=hours, max_rows=rows)


class PaperBroker:
    """Simulateur local : marche aléatoire, paiement 92 %. Pour tester le bot sans compte ni argent."""

    SYMBOLS = [("EURUSD_otc", "EUR/USD OTC", "currency"), ("EURNZD_otc", "EUR/NZD OTC", "currency"),
               ("AEDCNY_otc", "AED/CNY OTC", "currency"), ("BTCUSD_otc", "Bitcoin OTC", "crypto"),
               ("AAPL_otc", "Apple OTC", "other")]

    def __init__(self, ssid: str = "", start_balance: float = 50000.0, tick: float = 0.25):
        self._bal = start_balance
        self._tick = tick
        self._price: Dict[str, float] = {s: 1.0 + random.random() for s, _, _ in self.SYMBOLS}
        self._ticks: Dict[str, List[Tuple[float, float]]] = {s: [] for s, _, _ in self.SYMBOLS}
        self._task: Optional[asyncio.Task] = None
        self._open: Dict[str, dict] = {}
        self._n = 0

    async def start(self) -> None:
        now = time.time()                       # historique simulé : 2 h50 de ticks à 1 s
        for s in self._price:
            p = self._price[s]
            for i in range(10000, 0, -1):
                p *= math.exp(random.gauss(0, 0.0004))
                self._ticks[s].append((now - i, p))
            self._price[s] = p
        self._task = asyncio.create_task(self._walk())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def shutdown(self) -> None:
        await self.stop()

    async def _walk(self) -> None:
        while True:
            now = time.time()
            for s in self._price:
                self._price[s] *= math.exp(random.gauss(0, 0.0004))
                self._ticks[s].append((now, self._price[s]))
                del self._ticks[s][:-12000]
            await asyncio.sleep(self._tick)

    def is_connected(self) -> bool: return True
    async def reconnect(self) -> None: return None
    def is_demo(self) -> bool: return True
    async def balance(self) -> float: return self._bal

    async def assets(self) -> List[AssetInfo]:
        return [AssetInfo(s, n, 92 - i, c, True) for i, (s, n, c) in enumerate(self.SYMBOLS)]

    async def place(self, asset: str, direction: str, amount: float, expiration: int) -> str:
        self._n += 1
        tid = f"paper-{self._n}"
        self._bal -= amount
        self._open[tid] = dict(asset=asset, d=direction, amt=amount, exp=expiration,
                               open=self._price[asset], t=time.time())
        return tid

    async def result(self, trade_id: str, expiration: int, amount: float) -> TradeResult:
        o = self._open.pop(trade_id)
        await asyncio.sleep(max(0.0, o["t"] + o["exp"] - time.time()))
        now = self._price[o["asset"]]
        won = now > o["open"] if o["d"] == "call" else now < o["open"]
        if now == o["open"]:
            self._bal += o["amt"]
            return TradeResult("draw", 0.0)
        if won:
            gain = o["amt"] * 0.92
            self._bal += o["amt"] + gain
            return TradeResult("win", gain)
        return TradeResult("loss", -o["amt"])

    async def candles(self, asset: str, period: int, rows: int = 80):
        while True:
            ticks = list(self._ticks[asset])
            if len(ticks) > 5:
                buckets: Dict[int, List[float]] = {}
                for ts, p in ticks:
                    buckets.setdefault(int(ts // period) * period, []).append(p)
                keys = sorted(buckets)
                mk = lambda k: {"time": k, "open": buckets[k][0], "high": max(buckets[k]),
                                "low": min(buckets[k]), "close": buckets[k][-1]}
                yield [mk(k) for k in keys[:-1]][-rows:], mk(keys[-1])
            await asyncio.sleep(self._tick)


def make_broker(kind: str, ssid: str):
    return PaperBroker(ssid) if kind == "paper" else PocketBroker(ssid)
