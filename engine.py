"""Moteur de trading : scanner de signaux, martingale par confirmation, garde-fous.

Martingale (toutes les stratégies) : après une PERTE on n'ouvre rien tout de suite ; on attend la confirmation
suivante de la MÊME stratégie, puis la mise est multipliée par 2,1 ; puis 2,2 ; 2,3 ; 2,4 ; 2,5 (à chaque nouvelle
perte). Un gain remet la mise de base. Après le dernier palier perdu, la session s'arrête. Elle s'arrête aussi au
take-profit / stop-loss : un palier dont la mise ferait dépasser le stop-loss n'est jamais ouvert.

Limite propre aux options binaires : une position ouverte ne peut pas être clôturée avant son expiration. Les
seuils agissent donc sur la SESSION : dès qu'un seuil est franchi, plus aucune nouvelle position n'est ouverte ;
les positions déjà ouvertes vont à leur terme puis la session se clôture.
"""
import asyncio
import copy
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

import pandas as pd

from analysis import trend
from broker import AssetInfo
from store import Store, UserSettings
from strategies import REGISTRY, StrategyInfo
from strategies.signal import Signal

log = logging.getLogger("engine")

Notify = Callable[..., Awaitable[None]]

# Vitesse d'entrée : intervalle mini (s) entre deux évaluations d'un flux ; "cloture" = à la clôture de bougie.
SPEED_INTERVAL = {"ultra": 0.02, "rapide": 0.12, "normal": 0.5}

MG_FACTORS = (2.1, 2.2, 2.3, 2.4, 2.5)      # multiplicateur de la mise à chaque palier de martingale


@dataclass
class Position:
    deal_id: int
    step: int
    asset: str
    direction: str
    amount: float
    expiration: int
    strategy: str
    opened_at: float = 0.0
    trade_id: str = ""
    status: str = "pending"      # pending | open | win | loss | draw | error
    profit: float = 0.0
    latency_ms: float = 0.0      # décision du signal -> ordre accepté par le courtier


@dataclass
class Deal:
    """Un trade = un deal (une seule position)."""
    id: int
    asset: str
    direction: str
    strategy: str
    amount: float
    payout: int
    expiration: int
    level: int = 0               # 0 = mise de base ; 1..5 = palier de martingale
    ref: object = None           # manuel : identifiant du signal d'origine
    pos: Optional[Position] = None
    status: str = "running"      # running | won | lost | draw | aborted
    abort_reason: str = ""
    win_no: int = 0              # rang du deal gagnant dans la session (1, 2, 3…), 0 si non gagné

    @property
    def positions(self) -> List[Position]:
        return [self.pos] if self.pos else []

    @property
    def pnl(self) -> float:
        return self.pos.profit if self.pos and self.pos.status in ("win", "loss", "draw") else 0.0


class Engine:
    WATCHDOG_S = 5.0           # période de surveillance de la connexion
    MAX_RECONNECT_FAILS = 4    # échecs consécutifs avant arrêt de la session

    def __init__(self, uid: int, cfg: UserSettings, broker, store: Store, notify: Notify,
                 stake_cap: Optional[float] = None):
        self.uid = uid
        self.cfg = copy.deepcopy(cfg)          # instantané : les réglages ne changent pas en cours de session
        self.broker = broker
        self.store = store
        self._notify = notify
        self.stake_cap = stake_cap             # plafond de mise (mode réel)

        self.kind = "signals"                  # "auto" | "signals" | "manual"
        self.execute = False
        self.running = False
        self.stopping = False
        self.stop_reason = ""
        self._finished = False

        self.assets: List[AssetInfo] = []
        self.payouts: Dict[str, int] = {}
        self.names: Dict[str, str] = {}
        self.balance = 0.0
        self.start_balance = 0.0
        self.started_at = 0.0

        self.deals: List[Deal] = []
        self.open_deals: Dict[int, Deal] = {}
        self.deals_started = 0
        self.pnl = 0.0
        self.deals_won = 0
        self.deals_lost = 0
        self.deals_draw = 0
        self.signals_seen = 0
        self.mg: Dict[str, dict] = {}          # stratégie -> {"level": n, "amount": prochaine mise}
        self.sigs: Dict[int, tuple] = {}       # manuel : id de signal -> (signal, info, horodatage)
        self._placed: set = set()
        self._sid = 0
        self.latencies: List[float] = []

        self._feeds: Dict[tuple, asyncio.Task] = {}
        self._tasks: List[asyncio.Task] = []
        self._fired: Dict[tuple, object] = {}
        self._last_open: Dict[str, float] = {}
        self._deal_seq = 0
        self._down = False
        self._fails = 0
        self._notified: Dict[tuple, object] = {}
        self.changed = asyncio.Event()         # levé à chaque événement : le tableau de bord se rafraîchit aussitôt

    # ------------------------------------------------------------------ cycle de vie
    async def prepare(self) -> None:
        await self._refresh_assets()
        self.balance = self.start_balance = await self.broker.balance()

    async def start(self, execute: bool, kind: Optional[str] = None) -> None:
        self.kind = kind or ("auto" if execute else "signals")
        self.execute = execute
        await self.prepare()
        self.running, self.stopping, self._finished = True, False, False
        self.started_at = time.time()
        self._tasks = [asyncio.create_task(self._refresh_loop()),
                       asyncio.create_task(self._scan_loop()),
                       asyncio.create_task(self._watchdog())]

    def stop(self, reason: str = "Arrêt manuel") -> None:
        self._stop(reason)

    async def shutdown(self) -> None:
        self.running = False
        for t in list(self._feeds.values()) + self._tasks:
            t.cancel()
        self._feeds.clear()
        self._tasks.clear()

    # ------------------------------------------------------------------ actifs
    async def _refresh_assets(self) -> None:
        self.assets = await self.broker.assets()
        self.payouts = {a.symbol: a.payout for a in self.assets if a.active}
        self.names = {a.symbol: a.name for a in self.assets}

    async def _refresh_loop(self) -> None:
        while self.running:
            await asyncio.sleep(30)
            try:
                await self._refresh_assets()
            except Exception as e:
                log.warning("refresh assets: %s", e)

    async def _watchdog(self) -> None:
        """Surveille la connexion : un seul message à la coupure, un seul au retour, arrêt propre si SSID mort."""
        while self.running:
            await asyncio.sleep(self.WATCHDOG_S)
            try:
                if self.broker.is_connected():
                    if self._down:
                        self._down, self._fails = False, 0
                        self._emit("info", text="✅ Connexion Pocket Option rétablie.")
                    if not self.open_deals:
                        self.balance = await self.broker.balance()
                    continue
                if not self._down:
                    self._down = True
                    log.warning("connexion perdue -> reconnexion")
                    self._emit("info", text="⚠️ Connexion Pocket Option perdue — reconnexion en cours…")
                try:
                    await self.broker.reconnect()
                    self._fails = 0
                except Exception as e:
                    self._fails += 1
                    log.warning("reconnexion %s/%s: %s", self._fails, self.MAX_RECONNECT_FAILS, e)
                    if self._fails >= self.MAX_RECONNECT_FAILS:
                        self._stop("🔌 Reconnexion impossible (SSID expiré ou session ouverte ailleurs ?). "
                                   "Renvoyez /ssid demo <SSID>")
                        return
            except Exception as e:
                log.warning("watchdog: %s", e)

    def pick_assets(self) -> List[str]:
        c = self.cfg
        if c.asset_mode == "manual" and c.asset:
            return [c.asset]
        want_otc = c.market == "otc"
        cands = [a for a in self.assets if a.active and a.is_otc == want_otc and a.payout >= c.min_payout
                 and (a.category == "currency" or not c.currencies_only)]
        cands.sort(key=lambda a: -a.payout)
        return [a.symbol for a in cands[: max(1, c.scan_assets)]]

    # ------------------------------------------------------------------ scanner
    async def _scan_loop(self) -> None:
        while self.running:
            tfs = {REGISTRY[s].timeframe for s in self.cfg.strategies if s in REGISTRY}
            wanted = {(sym, tf) for sym in self.pick_assets() for tf in tfs}
            for k in [k for k in self._feeds if k not in wanted]:
                self._feeds.pop(k).cancel()
            for k in wanted:
                t = self._feeds.get(k)
                if t is None or t.done():
                    self._feeds[k] = asyncio.create_task(self._feed(*k))
                    await asyncio.sleep(0.4)             # démarrage échelonné : évite de saturer la connexion
            await asyncio.sleep(10)

    @staticmethod
    def _df(rows: List[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"]).astype(
            {"open": float, "high": float, "low": float, "close": float})

    async def _feed(self, asset: str, tf: int) -> None:
        keys = [s for s in self.cfg.strategies if s in REGISTRY and REGISTRY[s].timeframe == tf]
        backoff, last_eval, last_closed = 1.0, 0.0, None
        min_rows = min(REGISTRY[k].min_candles for k in keys) if keys else 35
        while self.running:
            try:
                async for closed, forming in self.broker.candles(asset, tf):
                    if not self.running:
                        return
                    backoff = 1.0
                    if self.cfg.speed != "cloture":             # évaluation en cours de bougie (bougie en formation)
                        now = time.monotonic()
                        if now - last_eval < SPEED_INTERVAL.get(self.cfg.speed, 0.12):
                            continue
                        last_eval = now
                        rows = closed + ([forming] if forming else [])
                        key = forming["time"] if forming else None
                    else:                                       # évaluation dès la clôture d'une bougie
                        ck = closed[-1]["time"] if closed else None
                        if ck is None or ck == last_closed:
                            continue
                        last_closed, rows, key = ck, closed, ck
                    if len(rows) < min_rows:
                        continue
                    df = self._df(rows)
                    df.attrs["forming"] = self.cfg.speed != "cloture" and bool(forming)
                    self._evaluate(asset, df, key, keys)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._down or not self.broker.is_connected():     # coupure déjà signalée par le watchdog
                    await asyncio.sleep(2.0)
                    continue
                log.warning("flux %s/%ss: %s", asset, tf, e)
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    def _evaluate(self, asset: str, df: pd.DataFrame, key, strat_keys: List[str]) -> None:
        t0 = time.perf_counter()
        for sk in strat_keys:
            if key is not None and self._fired.get((asset, sk)) == key:
                continue                                          # un seul signal par bougie et par stratégie
            info = REGISTRY[sk]
            try:
                sig = info.analyze(df, asset)
            except Exception:
                log.exception("stratégie %s", sk)
                continue
            if sig is None:
                continue
            self.on_signal(sig, info, df, t0, key=(asset, sk, key))

    # ------------------------------------------------------------------ signal -> ordre
    def on_signal(self, sig: Signal, info: StrategyInfo, df: pd.DataFrame, t0: float, key=None) -> Optional[Deal]:
        """`key` = (actif, stratégie, bougie). Auto : un message par trade réellement ouvert ; si l'ordre est bloqué
        (un trade déjà en cours sur la stratégie, positions max…) l'entrée est retentée tant que le signal tient.
        Signaux / manuel : un message par bougie et par stratégie."""
        c = self.cfg
        if sig.confidence < c.min_confidence:
            return None
        payout = self.payouts.get(sig.pair, 0)
        if payout < c.min_payout:
            return None
        tr = trend(df)
        if c.trend_filter and ((sig.direction == "call" and tr["dir"] == "down")
                               or (sig.direction == "put" and tr["dir"] == "up")):
            return None
        name = self.names.get(sig.pair, sig.pair)
        if self.execute:
            deal = self._open_deal(sig, info, payout, t0)
            if deal is None:
                return None
            if key is not None:
                self._fired[(key[0], key[1])] = key[2]
            self.signals_seen += 1
            self._emit("signal", sig=sig, info=info, payout=payout, trend=tr, name=name, deal=deal, sid=None)
            return deal
        if key is not None:
            if self._fired.get((key[0], key[1])) == key[2]:
                return None
            self._fired[(key[0], key[1])] = key[2]
        self.signals_seen += 1
        sid = None
        if self.kind == "manual":
            self._sid += 1
            sid = self._sid
            self.sigs[sid] = (sig, info, time.time())
            for old in [k for k in self.sigs if k < sid - 20]:
                self.sigs.pop(old, None)
        self._emit("signal", sig=sig, info=info, payout=payout, trend=tr, name=name, deal=None, sid=sid)
        return None

    def _gate_new(self, asset: str, strategy: str) -> Optional[str]:
        """Raisons TRANSITOIRES de ne pas ouvrir maintenant (le signal sera retenté)."""
        c = self.cfg
        if not self.running or self.stopping:
            return "session arrêtée"
        if c.session_mode == "series" and self.deals_won + len(self.open_deals) >= c.series_deals:
            return "série complète"                 # la série compte des deals GAGNANTS
        if len(self.open_deals) >= c.max_open:
            return "positions max atteintes"
        if any(d.strategy == strategy for d in self.open_deals.values()):
            return "trade déjà en cours sur cette stratégie"      # la mise dépend du résultat du précédent
        if sum(1 for d in self.open_deals.values() if d.asset == asset) >= c.max_open_per_asset:
            return "déjà une position sur cet actif"
        if time.time() - self._last_open.get(asset, 0) < c.cooldown_s:
            return "délai mini entre entrées"
        return None

    def _gate_amount(self, amount: float) -> Optional[str]:
        if self.stake_cap is not None and amount > self.stake_cap:
            return f"mise > plafond réel (${self.stake_cap:.0f})"
        if amount > self.balance:
            return "solde insuffisant"
        return None

    def _next_amount(self, strategy: str, info: StrategyInfo):
        """(mise, palier) du prochain trade de la stratégie."""
        base = round(self.cfg.stake, 2)
        st = self.mg.get(strategy)
        if not (self.cfg.mg_enabled and info.martingale) or not st or st["level"] <= 0:
            return base, 0
        return round(st["amount"], 2), st["level"]

    def _open_deal(self, sig: Signal, info: StrategyInfo, payout: int, t0: float) -> Optional[Deal]:
        c = self.cfg
        why = self._gate_new(sig.pair, sig.strategy)
        if why:
            log.info("signal %s %s ignoré: %s", sig.strategy, sig.pair, why)
            return None
        amount, level = self._next_amount(sig.strategy, info)
        why = self._gate_amount(amount)
        if not why and level > 0 and c.stop_loss > 0 and self.pnl - amount < -c.stop_loss:
            why = f"la mise ${amount:g} dépasserait le stop-loss (−${c.stop_loss:g})"
        if why:
            log.info("signal %s %s ignoré: %s", sig.strategy, sig.pair, why)
            if level > 0:                              # palier de martingale impossible -> on arrête proprement
                self._stop(f"⛔ Martingale interrompue ({sig.strategy}) : {why}")
            return None
        self._deal_seq += 1
        deal = Deal(self._deal_seq, sig.pair, sig.direction, sig.strategy, amount, payout, sig.expiration, level)
        self.deals.append(deal)
        self.open_deals[deal.id] = deal
        self.deals_started += 1
        self._last_open[sig.pair] = time.time()
        asyncio.create_task(self._run_deal(deal, t0))
        return deal

    async def place_signal(self, sid: int, amount: float) -> Deal:
        """Manuel : place le trade correspondant à un signal affiché (bouton « Placer le trade »)."""
        item = self.sigs.get(sid)
        if item is None:
            raise ValueError("Signal expiré")
        sig, info, ts = item
        if sid in self._placed:
            raise ValueError("Trade déjà placé pour ce signal")
        if time.time() - ts > info.max_delay:
            raise ValueError("Signal expiré — attendez le suivant")
        if not self.payouts:
            await self._refresh_assets()
        amount = round(amount, 2)
        if amount > self.balance:
            self.balance = await self.broker.balance()
        why = self._gate_amount(amount)
        if why:
            raise ValueError(why)
        self._placed.add(sid)
        self._deal_seq += 1
        deal = Deal(self._deal_seq, sig.pair, sig.direction, sig.strategy, amount,
                    self.payouts.get(sig.pair, 0), sig.expiration, 0, ref=sid)
        self.deals.append(deal)
        self.open_deals[deal.id] = deal
        self.deals_started += 1
        asyncio.create_task(self._run_deal(deal, time.perf_counter()))
        return deal

    # ------------------------------------------------------------------ exécution d'un trade
    def _mg_update(self, deal: Deal, outcome: str) -> None:
        """Met à jour la martingale de la stratégie après un résultat (auto uniquement)."""
        if self.kind != "auto" or not self.cfg.mg_enabled or not REGISTRY[deal.strategy].martingale:
            return
        if outcome == "win":
            self.mg.pop(deal.strategy, None)
        elif outcome == "loss":
            if deal.level >= len(MG_FACTORS):
                self.mg.pop(deal.strategy, None)
                self._stop(f"🛑 Martingale épuisée ({deal.strategy} : {len(MG_FACTORS) + 1} pertes de suite)")
            else:
                self.mg[deal.strategy] = {"level": deal.level + 1,
                                          "amount": round(deal.amount * MG_FACTORS[deal.level], 2)}
        # égalité : la mise est remboursée, le palier ne change pas

    async def _run_deal(self, deal: Deal, t0: float) -> None:
        try:
            pos = Position(deal.id, deal.level + 1, deal.asset, deal.direction, deal.amount, deal.expiration, deal.strategy)
            deal.pos = pos
            try:
                pos.trade_id = await self.broker.place(deal.asset, deal.direction, deal.amount, deal.expiration)
            except Exception as e:
                pos.status, deal.status, deal.abort_reason = "error", "aborted", f"ordre refusé: {e}"
                self._emit("error", text=f"Ordre refusé sur {deal.asset} : {e}", deal=deal)
                return
            pos.status, pos.opened_at = "open", time.time()
            pos.latency_ms = (time.perf_counter() - t0) * 1000.0
            self.latencies.append(pos.latency_ms)
            self.balance -= deal.amount
            self._emit("position_open", deal=deal, pos=pos)
            try:
                res = await self.broker.result(pos.trade_id, deal.expiration, deal.amount)
            except Exception as e:
                pos.status, deal.status, deal.abort_reason = "error", "aborted", f"résultat indisponible: {e}"
                self._emit("error", text=f"Résultat introuvable ({deal.asset}) : {e}", deal=deal)
                return
            pos.status, pos.profit = res.outcome, res.profit
            self.balance += deal.amount + res.profit
            self.pnl += res.profit
            self.store.log_trade(self.uid, dict(
                strategy=deal.strategy, asset=deal.asset, dir=deal.direction, step=deal.level + 1, amount=deal.amount,
                outcome=res.outcome, profit=round(res.profit, 2), exp=deal.expiration,
                latency_ms=round(pos.latency_ms), payout=deal.payout, demo=self.cfg.mode == "demo",
                manual=self.kind == "manual"))
            deal.status = {"win": "won", "loss": "lost"}.get(res.outcome, "draw")
            self._emit("position_closed", deal=deal, pos=pos)
            self._mg_update(deal, res.outcome)
            self._check_limits()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("deal %s", deal.id)
            deal.status = "aborted"
        finally:
            if deal.status == "running":
                deal.status = "aborted"
            self.open_deals.pop(deal.id, None)
            self._on_deal_done(deal)

    # ------------------------------------------------------------------ seuils & fin de session
    def _check_limits(self) -> None:
        c = self.cfg
        if self.kind != "auto":
            return
        if c.take_profit > 0 and self.pnl >= c.take_profit:
            self._stop(f"🎯 Take-profit atteint (+${self.pnl:.2f})")
        elif c.stop_loss > 0 and self.pnl <= -c.stop_loss:
            self._stop(f"🛑 Stop-loss atteint (−${abs(self.pnl):.2f})")

    def _on_deal_done(self, deal: Deal) -> None:
        if deal.status == "won":
            self.deals_won += 1
            deal.win_no = self.deals_won
        elif deal.status == "lost":
            self.deals_lost += 1
        elif deal.status == "draw":
            self.deals_draw += 1
        self._emit("deal_closed", deal=deal)
        self._maybe_finish()

    def _stop(self, reason: str) -> None:
        if self.stopping:
            return
        self.stopping, self.stop_reason = True, reason
        self._emit("info", text=f"{reason} — plus aucune nouvelle position. "
                                f"{len(self.open_deals)} deal(s) en cours vont à terme.")
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if self._finished or self.kind not in ("auto", "signals", "manual") or not self.running:
            return
        series_done = (self.kind == "auto" and self.cfg.session_mode == "series"
                       and self.deals_won >= self.cfg.series_deals)
        if (self.stopping or series_done) and not self.open_deals:
            self._finished = True
            if not self.stop_reason:
                self.stop_reason = "🏁 Série terminée"
            asyncio.create_task(self._end())

    async def _end(self) -> None:
        await self.shutdown()
        self._emit("session_end")

    # ------------------------------------------------------------------ utilitaires
    def _emit(self, kind: str, **data) -> None:
        self.changed.set()
        async def _go():
            try:
                await self._notify(kind, engine=self, **data)
            except Exception:
                log.exception("notify %s", kind)
        try:
            asyncio.get_running_loop().create_task(_go())
        except RuntimeError:
            pass

    @property
    def winrate(self) -> Optional[float]:
        n = self.deals_won + self.deals_lost
        return None if n == 0 else 100.0 * self.deals_won / n

    @property
    def avg_latency(self) -> Optional[float]:
        return sum(self.latencies) / len(self.latencies) if self.latencies else None
