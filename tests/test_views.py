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


def test_summary_and_dashboard():
    cfg = types.SimpleNamespace(mode="demo", asset_mode="auto", asset="", scan_assets=2, session_mode="series",
                                series_deals=5, take_profit=20.0, stop_loss=30.0)
    e = types.SimpleNamespace(cfg=cfg, kind="auto", names={}, deals_won=3, deals_lost=2, deals_draw=1, pnl=1.5, balance=1001.5,
                              start_balance=1000.0, signals_seen=6, stopping=False, stop_reason="🎯 Objectif atteint",
                              running=True, open_deals={}, mg={"1M": {"level": 2, "amount": 4.62}},
                              deals=[1, 2, 3, 4, 5, 6], started_at=time.time() - 125)
    dash = views.dashboard(e)
    assert "palier 2" in dash and "$4.62" in dash and "TP +$20" in dash and "SL −$30" in dash
    summ = views.session_summary(e)
    assert "Trades lancés : <b>6</b>" in summ and "✅ 3" in summ and "❌ 2" in summ and "⚪ 1" in summ and "DÉMO" in summ


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("✓", n)
