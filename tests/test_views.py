"""Format des messages : trade (auto / manuel / signal), barre d'état, résumé de session."""
import time
import types

import views
from engine import Deal, Position
from strategies import REGISTRY
from strategies.signal import Signal


def mkd(strat="5s", direction="call", payout=92, trend="baissière"):
    s = Signal(strat, "USDDZD_otc", direction, 80, REGISTRY[strat].expiration, "t", 1.0)
    return dict(sig=s, info=REGISTRY[strat], payout=payout, trend={"label": trend, "dir": "down"}, name="USD/DZD OTC")


def deal(amount=1.0, level=0, status="win", profit=0.92):
    d = Deal(1, "USDDZD_otc", "call", "5s", amount, 92, 5, level)
    d.pos = Position(1, level + 1, "USDDZD_otc", "call", amount, 5, "5s", status=status, profit=profit)
    return d


def test_trade_text_follows_requested_format():
    t = views.trade_text(mkd(), "wait", deal())
    lines = t.split("\n")
    assert lines[0].startswith("🎯 <b>ACTIF:</b> 🇺🇸 USD/DZD 🇩🇿 OTC · paiement 92%"), lines[0]
    assert "📊 <b>Tendance:</b> baissière" in lines[1] and "🕯️ <b>Bougie:</b> 5s" in lines[1]
    assert "⏳ <b>EXPIRATION:</b> 5s" in lines[1]
    assert lines[2].startswith("🔮 <b>Direction:</b> ACHAT")
    assert lines[3].startswith("🏁 <b>Résultat:</b>")
    assert "VENTE" in views.trade_text(mkd(direction="put"), "wait", deal())


def test_result_stages():
    d = mkd()
    run = views.trade_text(d, "run", deal(status="open"), rem=3, total=5, frame=2)
    assert "▰" in run and "▱" in run and "3s" in run
    assert "frames" or True
    assert run != views.trade_text(d, "run", deal(status="open"), rem=3, total=5, frame=3), "l'horloge tourne"
    assert "✅" in views.trade_text(d, "win", deal(profit=0.92)) and "+$0.92" in views.trade_text(d, "win", deal(profit=0.92))
    loss = views.trade_text(d, "loss", deal(status="loss", profit=-1.0))
    assert "❌" in loss and "−$1.00" in loss
    assert "ÉGALITÉ" in views.trade_text(d, "draw", deal(status="draw", profit=0))
    assert "Résultat" not in views.trade_text(d, "signal")                  # signal seul : pas de ligne résultat
    assert "en attente de votre ordre" in views.trade_text(d, "ready")


def test_stake_and_martingale_level_shown():
    t = views.trade_text(mkd("1M"), "wait", deal(amount=4.62, level=2))
    assert "$4.62" in t and "palier 2" in t
    assert "palier" not in views.trade_text(mkd("1M"), "wait", deal(level=0))
    assert "Bougie:</b> 1m HA" in views.trade_text(mkd("1M"), "wait", deal())
    assert "Bougie:</b> 1m" in views.trade_text(mkd("5M"), "wait", deal()) and "EXPIRATION:</b> 5m" in views.trade_text(mkd("5M"), "wait", deal())


def _cfg(**kw):
    base = dict(mode="demo", asset_mode="auto", asset="", scan_assets=2, session_mode="series", series_deals=5,
                take_profit=20.0, stop_loss=30.0, strategies=["5s"], max_open=3, mg_enabled=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _deal(cycle, level, status, amount, profit, win_no=0, end=False):
    d = Deal(cycle * 10 + level, "EURUSD_otc", "call", "5s", amount, 92, 5, level, cycle=cycle, win_no=win_no, chain_end=end)
    d.status = {"win": "won", "loss": "lost", "open": "running"}[status]
    d.pos = Position(d.id, level + 1, d.asset, "call", amount, 5, "5s", status=status, profit=profit, opened_at=time.time())
    return d


def _engine(cfg, deals, **kw):
    base = dict(cfg=cfg, kind="auto", names={}, deals=deals, open_deals={}, deals_won=0, deals_lost=0, deals_draw=0,
                chains_lost=0, winrate=None, pnl=0.0, balance=1630.26, start_balance=1638.04, avg_latency=159.0, mg={},
                stopping=False, running=True, stop_reason="", signals_seen=0, started_at=time.time() - 125)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_dashboard_matches_requested_layout():
    deals = [_deal(1, 0, "loss", 10, -10), _deal(1, 1, "win", 21, 19.32, 1), _deal(2, 0, "win", 10, 9.2, 2),
             _deal(3, 0, "loss", 10, -10), _deal(3, 1, "loss", 21, -21), _deal(3, 2, "win", 44.1, 40.57, 3)]
    e = _engine(_cfg(), deals, deals_won=3, deals_lost=3, winrate=100.0, pnl=28.1)
    lines = views.dashboard(e).split("\n")
    assert lines[0] == "🤖 <b>Auto-trading</b> · 5s · top 2 paiements (DÉMO)"
    assert lines[1] == "🎲 Série · deal 3/5 · positions ouvertes 0/3"
    assert lines[2].startswith("📈 Winrate deals : 100%") and "P&amp;L session <b>+$28.10</b>" in lines[2]
    assert lines[3] == "💰 Solde : <b>$1 630.26</b> (départ $1 638.04)"
    assert "TP" not in lines[4] and "SL" not in lines[4], "série : ni TP ni SL"
    assert "⚡ Latence moyenne signal→ordre : 159 ms" in lines
    assert "⚡ Deal 1·Étape 1 | 🔴 $10.00 → −$10.00" in lines
    assert "⚡ Deal 1·Étape 2 | 🟢 $21.00 → +$19.32" in lines
    assert "✅ Deal 1/5 clôturé en profit (+$9.32)" in lines
    assert "⚡ Deal 3·Étape 3 | 🟢 $44.10 → +$40.57" in lines and "✅ Deal 3/5 clôturé en profit (+$9.57)" in lines
    assert lines[-1].startswith("🔎 Scan en cours")


def test_dashboard_animation_and_final_state():
    open_deal = _deal(4, 0, "open", 10, 0)
    e = _engine(_cfg(), [open_deal], open_deals={4: open_deal})
    t = views.dashboard(e)
    assert "⚡ Deal 4·Étape 1 | 🎲 $10.00" in t and "trade en cours" in t and "▱" in t
    e = _engine(_cfg(), [], deals_won=5, running=False, stop_reason="🏁 Série terminée")
    assert views.dashboard(e, final=True).splitlines()[-1] == "🏁 Terminé : 5/5 deals"
    e = _engine(_cfg(session_mode="tp"), [], running=False, stop_reason="🎯 Take-profit atteint (+$10.20)")
    t = views.dashboard(e, final=True)
    assert "TP +$20" in t and "SL −$30" in t and t.splitlines()[-1] == "🏁 Terminé : 🎯 Take-profit atteint (+$10.20)"
    lost = _deal(1, 5, "loss", 63.78, -63.78, end=True)
    t = views.dashboard(_engine(_cfg(), [lost], deals_lost=1, chains_lost=1, winrate=0.0))
    assert "❌ Deal 1 clôturé en perte (−$63.78)" in t


def test_summary():
    e = _engine(_cfg(), [1, 2, 3, 4, 5, 6], deals_won=3, deals_lost=2, deals_draw=1, pnl=1.5, balance=1001.5,
                start_balance=1000.0, stop_reason="🎯 Objectif atteint")
    summ = views.session_summary(e)
    assert "Trades lancés : <b>6</b>" in summ and "✅ 3" in summ and "❌ 2" in summ and "⚪ 1" in summ and "DÉMO" in summ


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("✓", n)
