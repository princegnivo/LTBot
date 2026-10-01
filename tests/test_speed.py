"""Vérifie les 4 vitesses sur le simulateur : le nombre d'évaluations doit décroître avec la vitesse."""
import asyncio, tempfile
from broker import PaperBroker
from engine import Engine
from store import Store, UserSettings


async def run(speed):
    async def notify(kind, **d): pass
    with tempfile.TemporaryDirectory() as tmp:
        b = PaperBroker(tick=0.01); await b.start()
        e = Engine(1, UserSettings(speed=speed, scan_assets=1, strategies=["5s"]), b, Store(tmp), notify)
        n = {"v": 0}
        orig = e._evaluate
        def counted(*a, **k):
            n["v"] += 1
            return orig(*a, **k)
        e._evaluate = counted
        await e.start(False)
        await asyncio.sleep(6)
        await e.shutdown(); await b.stop()
        return n["v"]


async def main():
    res = {s: await run(s) for s in ("ultra", "rapide", "normal", "cloture")}
    print(res)
    assert res["ultra"] > res["rapide"] > res["normal"] > res["cloture"], res

asyncio.run(main())
