"""Rendu texte (HTML Telegram) : signaux, tableau de bord de session, comptes, stats."""
import html
import time
from typing import Dict, List

E = html.escape
DIR = {"call": ("🟢", "HAUSSE"), "put": ("🔴", "BAISSE")}
OUT = {"win": "🟢", "loss": "🔴", "draw": "⚪", "error": "⚠️", "open": "🎲", "pending": "🎲"}


def money(x: float, sign: bool = False) -> str:
    s = f"{abs(x):,.2f}".replace(",", " ")
    if sign:
        return f"{'+' if x > 0 else '−' if x < 0 else ''}${s}"
    return f"{'−' if x < 0 else ''}${s}"


def exp_label(sec: int) -> str:
    return f"{sec}s" if sec < 60 else f"{sec // 60}m" if sec % 60 == 0 else f"{sec}s"


def signal_text(d: Dict) -> str:
    sig, tr = d["sig"], d["trend"]
    em, lab = DIR[sig.direction]
    tag = "⚡ <b>Entrée immédiate</b>" if d.get("executed") else "📡 <b>Signal</b>"
    return (f"{tag} · <b>{E(sig.strategy)}</b>\n"
            f"🎯 <b>{E(d['name'])}</b> · paiement {d['payout']}%\n"
            f"{em} <b>{lab}</b> · expiration {exp_label(sig.expiration)} · confiance {sig.confidence}%\n"
            f"📊 tendance {tr['label']} : {tr['text']}\n"
            f"🧠 {E(sig.reason)}")


def _bar(remaining: float, total: float, n: int = 12) -> str:
    done = 0 if total <= 0 else max(0, min(n, round((1 - remaining / total) * n)))
    return "▰" * done + "▱" * (n - done)


def dashboard(e) -> str:
    c = e.cfg
    mode = "DÉMO" if c.mode == "demo" else "RÉEL"
    head = "🤖 <b>Auto-trading</b>" if e.kind == "auto" else "📡 <b>Signaux</b>"
    scope = (E(e.names.get(c.asset, c.asset)) if c.asset_mode == "manual" and c.asset
             else f"auto · top {c.scan_assets} paiements")
    lines = [f"{head} · {scope} ({mode})"]
    if e.kind == "auto":
        strat = "Série" if c.session_mode == "series" else "Take-profit"
        prog = f"deal {min(e.deals_started, c.series_deals)}/{c.series_deals}" if c.session_mode == "series" \
            else f"{e.deals_started} deals"
        lines.append(f"🎲 {strat} · {prog} · positions ouvertes {len(e.open_deals)}/{c.max_open}")
        wr = f"{e.winrate:.0f}%" if e.winrate is not None else "—"
        lines.append(f"📈 Winrate deals : {wr} · P&amp;L session <b>{money(e.pnl, True)}</b>")
        lines.append(f"💰 Solde : <b>{money(e.balance)}</b> (départ {money(e.start_balance)})")
        limits = []
        if c.take_profit > 0: limits.append(f"TP +${c.take_profit:g}")
        if c.stop_loss > 0: limits.append(f"SL −${c.stop_loss:g}")
        if c.max_consec_losses: limits.append(f"max {c.max_consec_losses} pertes d'affilée")
        if limits: lines.append("🛡 " + " · ".join(limits))
    else:
        lines.append(f"📡 Signaux détectés : {e.signals_seen} (aucun ordre passé)")
    if e.avg_latency is not None:
        lines.append(f"⚡ Latence moyenne signal→ordre : {e.avg_latency:.0f} ms")
    if e.stopping:
        lines.append(f"\n{E(e.stop_reason)}")
    lines.append("")
    shown = e.deals[-6:]
    for d in shown:
        for p in d.positions:
            lines.append(f"⚡ Deal {d.id}·Étape {p.step} | {OUT.get(p.status, '🎲')} {money(p.amount)}"
                         + (f" → {money(p.profit, True)}" if p.status in ("win", "loss", "draw") else "")
                         + f" · {E(d.strategy)} {E(e.names.get(d.asset, d.asset))} {DIR[d.direction][0]}")
        if d.status in ("won", "lost", "draw", "aborted"):
            tag = {"won": "✅ clôturé en profit", "lost": "❌ clôturé en perte", "draw": "⚪ égalité",
                   "aborted": "⛔ interrompu"}[d.status]
            lines.append(f"{tag} ({money(d.pnl, True)})" + (f" · {E(d.abort_reason)}" if d.abort_reason else ""))
    opens = [p for d in e.open_deals.values() for p in d.positions if p.status == "open"]
    if opens:
        p = min(opens, key=lambda x: x.opened_at + x.expiration)
        rem = max(0.0, p.opened_at + p.expiration - time.time())
        lines.append(f"\n⏱ trade en cours {_bar(rem, p.expiration)} {rem:.0f}s")
    elif e.running and not e.stopping:
        lines.append("\n🔎 Scan en cours… entrée dès que les conditions sont réunies.")
    return "\n".join(lines)


def session_summary(e) -> str:
    c = e.cfg
    n = e.deals_won + e.deals_lost
    steps = sum(len(d.positions) for d in e.deals)
    dur = int(time.time() - e.started_at)
    return (f"🏁 <b>Résultat</b>\n{E(e.stop_reason)}\n"
            f"Deals : {e.deals_started} (gagnés {e.deals_won} · perdus {e.deals_lost}) · positions : {steps}\n"
            f"Résultat : <b>{money(e.pnl, True)}</b> {'🎯' if e.pnl > 0 else ''}\n"
            f"Solde : {money(e.start_balance)} → <b>{money(e.balance)}</b>\n"
            f"Durée : {dur // 60}m{dur % 60:02d}s · mode {'DÉMO' if c.mode == 'demo' else 'RÉEL'}")


def stats_text(trades: List[dict], title: str) -> str:
    if not trades:
        return f"📈 <b>{title}</b>\n\nAucun trade enregistré."
    def agg(rows):
        w = sum(1 for r in rows if r["outcome"] == "win")
        l = sum(1 for r in rows if r["outcome"] == "loss")
        pnl = sum(r["profit"] for r in rows)
        wr = f"{100 * w / (w + l):.0f}%" if (w + l) else "—"
        return len(rows), w, l, wr, pnl
    n, w, l, wr, pnl = agg(trades)
    out = [f"📈 <b>{title}</b>", f"\nPositions : {n} · ✅ {w} · ❌ {l} · winrate {wr}", f"P&amp;L : <b>{money(pnl, True)}</b>", ""]
    for s in ("1M", "2M", "5s", "MAN"):
        rows = [r for r in trades if r["strategy"] == s]
        if rows:
            n, w, l, wr, pnl = agg(rows)
            out.append(f"• {s} : {n} pos · WR {wr} · {money(pnl, True)}")
    lat = [r["latency_ms"] for r in trades if r.get("latency_ms")]
    if lat:
        out.append(f"\n⚡ Latence moyenne : {sum(lat) / len(lat):.0f} ms")
    out.append("\n<i>Winrate par position : avec martingale, un deal gagné après une perte compte 1 ✅ et 1 ❌.</i>")
    return "\n".join(out)
