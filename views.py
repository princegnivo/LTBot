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


CUR_CC = {
    "EUR": "EU", "USD": "US", "GBP": "GB", "JPY": "JP", "CHF": "CH", "AUD": "AU", "CAD": "CA", "NZD": "NZ",
    "CNY": "CN", "AED": "AE", "TRY": "TR", "ZAR": "ZA", "MXN": "MX", "BRL": "BR", "RUB": "RU", "INR": "IN",
    "IDR": "ID", "SGD": "SG", "HKD": "HK", "NOK": "NO", "SEK": "SE", "DKK": "DK", "PLN": "PL", "HUF": "HU",
    "CZK": "CZ", "UAH": "UA", "IRR": "IR", "KES": "KE", "NGN": "NG", "EGP": "EG", "COP": "CO", "ARS": "AR",
    "CLP": "CL", "PHP": "PH", "THB": "TH", "VND": "VN", "MYR": "MY", "BHD": "BH", "SAR": "SA", "QAR": "QA",
    "JOD": "JO", "LBP": "LB", "OMR": "OM", "TND": "TN", "MAD": "MA", "DZD": "DZ", "YER": "YE", "SYP": "SY",
    "PKR": "PK", "BDT": "BD", "LKR": "LK", "KWD": "KW", "ILS": "IL", "RON": "RO", "BGN": "BG", "TWD": "TW",
    "KRW": "KR", "ISK": "IS", "GHS": "GH", "ETB": "ET",
}


def flag(cur: str) -> str:
    cc = CUR_CC.get(cur.upper(), "")
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc) if cc else ""


def asset_label(symbol: str, name: str = "") -> str:
    """'EURCHF_otc' -> '🇪🇺 EUR/CHF 🇨🇭 OTC' (actifs non-devises : nom tel quel)."""
    otc = symbol.lower().endswith("_otc") or "otc" in name.lower()
    core = symbol.lstrip("#").split("_")[0]
    if len(core) == 6 and core.isalpha():
        base, quote = core[:3].upper(), core[3:].upper()
        parts = [flag(base), f"{base}/{quote}", flag(quote), "OTC" if otc else ""]
        return " ".join(p for p in parts if p)
    return name or symbol


CLOCKS = "🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚🕛"


def _bar(remaining: float, total: float, n: int = 6) -> str:
    done = 0 if total <= 0 else max(0, min(n, round((1 - remaining / total) * n)))
    return "▰" * done + "▱" * (n - done)


def trade_text(d: Dict, stage: str, deal=None, rem: float = 0.0, total: float = 0.0, frame: int = 0) -> str:
    """Message unique d'un trade (auto, manuel ou signal seul), modifié sur place jusqu'au résultat.

    stage : signal (aucun ordre) | ready (manuel : en attente du bouton) | wait (ordre en cours d'envoi)
            run (animation) | check (expiré, résultat en cours) | win | loss | draw | error
    """
    sig, info, tr = d["sig"], d.get("info"), d.get("trend") or {}
    sens = "ACHAT" if sig.direction == "call" else "VENTE"
    candle = exp_label(info.timeframe if info else sig.expiration) + (" HA" if info is not None and info.ha else "")
    extra = ""
    if deal is not None:
        extra = f" · 💵 {money(deal.amount)}" + (f" · ♻️ palier {deal.level}" if deal.level > 0 else "")
    lines = [
        f"🎯 <b>ACTIF:</b> {E(asset_label(sig.pair, d.get('name', '')))} · paiement {d.get('payout', 0)}%",
        f"📊 <b>Tendance:</b> {E(tr.get('label', 'neutre'))} · 🕯️ <b>Bougie:</b> {candle} · "
        f"⏳ <b>EXPIRATION:</b> {exp_label(sig.expiration)}",
        f"🔮 <b>Direction:</b> {sens}{extra}",
    ]
    profit = deal.pnl if deal is not None else 0.0
    res = {
        "ready": "— en attente de votre ordre",
        "wait": "⏳ envoi de l'ordre…",
        "run": f"{CLOCKS[frame % 12]} {_bar(rem, total)} {max(0, rem):.0f}s",
        "check": "🕛 résultat en cours…",
        "win": f"✅ <b>GAIN {money(profit, True)}</b>",
        "loss": f"❌ <b>PERTE {money(profit, True)}</b>",
        "draw": "⚪ <b>ÉGALITÉ</b> · mise remboursée",
        "error": f"⚠️ {E(deal.abort_reason) if deal is not None else 'erreur'}",
    }.get(stage)
    if res:
        lines.append(f"🏁 <b>Résultat:</b> {res}")
    return "\n".join(lines)


def dashboard(e) -> str:
    """Barre d'état compacte d'une session auto / signaux (les trades ont leur propre message)."""
    c = e.cfg
    mode = "DÉMO" if c.mode == "demo" else "RÉEL"
    if e.kind != "auto":
        lines = [f"📡 <b>Signaux</b> · {mode}", f"Signaux détectés : {e.signals_seen} (aucun ordre passé)"]
    else:
        scope = (E(e.names.get(c.asset, c.asset)) if c.asset_mode == "manual" and c.asset
                 else f"auto · top {c.scan_assets}")
        lines = [f"🤖 <b>Auto-trading</b> · {scope} · {mode}"]
        goal = (f"série {e.deals_won}/{c.series_deals} gagnés" if c.session_mode == "series"
                else f"{e.deals_won} gagnés")
        lines.append(f"📈 {goal} · ❌ {e.deals_lost} · P&amp;L <b>{money(e.pnl, True)}</b> · 💰 {money(e.balance)}")
        limits = []
        if c.take_profit > 0: limits.append(f"TP +${c.take_profit:g}")
        if c.stop_loss > 0: limits.append(f"SL −${c.stop_loss:g}")
        if limits: lines.append("🛡 " + " · ".join(limits))
        for k, st in e.mg.items():
            lines.append(f"♻️ {E(k)} : palier {st['level']} · prochaine mise {money(st['amount'])} (après confirmation)")
    if e.stopping:
        lines.append(f"\n{E(e.stop_reason)}")
    elif e.running:
        lines.append("🔎 Scan en cours…" + (f" · {len(e.open_deals)} trade(s) ouvert(s)" if e.open_deals else ""))
    return "\n".join(lines)


def session_summary(e) -> str:
    c = e.cfg
    n = len(e.deals)
    done = e.deals_won + e.deals_lost + e.deals_draw
    dur = int(time.time() - e.started_at)
    wr = f" · winrate {100 * e.deals_won / (e.deals_won + e.deals_lost):.0f}%" if (e.deals_won + e.deals_lost) else ""
    return (f"🏁 <b>Résultat de la session</b>\n{E(e.stop_reason)}\n\n"
            f"Trades lancés : <b>{n}</b> · ✅ {e.deals_won} · ❌ {e.deals_lost} · ⚪ {e.deals_draw}"
            + (f" · ⚠️ {n - done}" if n - done > 0 else "") + f"{wr}\n"
            f"Résultat : <b>{money(e.pnl, True)}</b> {'🎯' if e.pnl > 0 else ''}\n"
            f"Solde : {money(e.start_balance)} → <b>{money(e.balance)}</b>\n"
            f"Durée : {dur // 60}m{dur % 60:02d}s · {'🟠 DÉMO' if c.mode == 'demo' else '🟢 RÉEL'}")


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
    out = [f"📈 <b>{title}</b>", f"\nTrades : {n} · ✅ {w} · ❌ {l} · winrate {wr}", f"P&amp;L : <b>{money(pnl, True)}</b>", ""]
    for s in ("5s", "1M", "2M", "5M"):
        rows = [r for r in trades if r["strategy"] == s]
        if rows:
            n, w, l, wr, pnl = agg(rows)
            out.append(f"• {s} : {n} trades · WR {wr} · {money(pnl, True)}")
    return "\n".join(out)
