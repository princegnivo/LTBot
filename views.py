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


def exp_long(sec: int) -> str:
    return f"{sec}s ({sec // 60}min)" if sec >= 60 and sec % 60 == 0 else f"{sec}s"


SEP_TOP = "_" * 30
SEP_BOTTOM = "—" * 19


def signal_text(d: Dict) -> str:
    sig = d["sig"]
    hhmm = time.strftime("%H:%M", time.localtime(sig.created_at))
    sens = "ACHAT" if sig.direction == "call" else "VENTE"
    return (f"{SEP_TOP}\n"
            f"📊 <b>ACTIF:</b> {E(asset_label(sig.pair, d.get('name', '')))}\n"
            f"🕘 <b>HEURE D'ENTRÉE:</b> {hhmm}\n"
            f"⏳ <b>EXPIRATION:</b> {exp_long(sig.expiration)}\n\n"
            f"🔮 Direction: <b>{sens}</b>\n\n"
            f"🔘<b>INFO:</b>\n"
            f"🧠{E(sig.reason)}\n"
            f"{SEP_BOTTOM}")


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
        prog = f"deals gagnés {e.deals_won}/{c.series_deals}" if c.session_mode == "series" \
            else f"deals gagnés {e.deals_won}"
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
    series = c.session_mode == "series"
    for d in e.deals[-8:]:
        name = E(e.names.get(d.asset, d.asset))
        if d.status == "running":
            for p in d.positions:
                lines.append(f"⚡ Deal en cours · étape {p.step} | {OUT.get(p.status, '🎲')} {money(p.amount)}"
                             + (f" → {money(p.profit, True)}" if p.status in ("win", "loss", "draw") else "")
                             + f" · {E(d.strategy)} {name} {DIR[d.direction][0]}")
            continue
        n = len(d.positions)
        who = f"{E(d.strategy)} {name} {DIR[d.direction][0]} · {n} étape{'s' if n > 1 else ''}"
        if d.status == "won":
            no = f"{d.win_no}/{c.series_deals}" if series else f"{d.win_no}"
            lines.append(f"✅ Deal {no} gagné · {who} · {money(d.pnl, True)}")
        elif d.status == "lost":
            lines.append(f"❌ Deal perdu · {who} · {money(d.pnl, True)}")
        elif d.status == "draw":
            lines.append(f"⚪ Égalité · {who}")
        else:
            lines.append(f"⛔ Deal interrompu · {who} · {E(d.abort_reason)}")
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
            f"Deals gagnés : {e.deals_won}" + (f"/{c.series_deals}" if c.session_mode == "series" else "")
            + f" · deals perdus : {e.deals_lost} · positions : {steps}\n"
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
