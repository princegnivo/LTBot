"""Contrôles avant démarrage : à lancer avec `python main.py --check` AVANT de mettre le bot en ligne sur un serveur."""
import asyncio
import importlib.util
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class Check:
    level: str          # ok | warn | error
    text: str


ICON = {"ok": "✅", "warn": "⚠️ ", "error": "❌"}


def _has(mod: str) -> bool:
    """Module importable ? (tolère les modules sans __spec__)"""
    if mod in sys.modules:
        return True
    try:
        return importlib.util.find_spec(mod) is not None
    except (ValueError, ImportError, AttributeError):
        return False


def static_checks(cfg) -> List[Check]:
    out: List[Check] = []
    add = lambda lvl, txt: out.append(Check(lvl, txt))
    if sys.version_info < (3, 10):
        add("error", f"Python {sys.version.split()[0]} trop ancien : 3.10 ou plus requis")
    else:
        add("ok", f"Python {sys.version.split()[0]}")
    for mod, pip, needed in (("telegram", "python-telegram-bot", True), ("pandas", "pandas", True), ("numpy", "numpy", True),
                             ("BinaryOptionsToolsV2", "BinaryOptionsToolsV2", cfg.broker == "pocketoption"),
                             ("playwright", "playwright", cfg.auto_signup or bool(cfg.partners_email))):
        if _has(mod):
            add("ok", f"module {mod}")
        else:
            add("error" if needed and mod != "playwright" else "warn", f"module {mod} absent : pip install {pip}")
    if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", cfg.telegram_token or ""):
        add("error", "TELEGRAM_TOKEN absent ou mal formé (attendu : 123456789:AA…)")
    else:
        add("ok", "TELEGRAM_TOKEN : format valide")
    add("ok" if cfg.admin_ids else "error", f"ADMIN_IDS : {len(cfg.admin_ids)} administrateur(s)" if cfg.admin_ids
        else "ADMIN_IDS vide : envoyez /id au bot, puis renseignez votre ID")
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = Path(cfg.data_dir) / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        free = shutil.disk_usage(cfg.data_dir).free // (1024 * 1024)
        add("ok" if free > 200 else "warn", f"dossier de données {cfg.data_dir} inscriptible · {free} Mo libres")
    except OSError as e:
        add("error", f"dossier de données {cfg.data_dir} non inscriptible : {e}")
    if cfg.require_verified and not cfg.po_register_url:
        add("warn", "PO_REGISTER_URL vide : boutons « S'INSCRIRE » et création de compte masqués")
    if cfg.verify_mode == "manual" and cfg.require_verified:
        add("warn", "VERIFY_MODE=manual : un admin doit valider chaque ID (renseignez PARTNERS_EMAIL/PASSWORD pour l'automatiser)")
    if "{amount}" not in cfg.deposit_url:
        add("warn", "DEPOSIT_URL ne contient pas {amount} : le montant ne sera pas transmis à la page de dépôt")
    if cfg.selectors_file and not Path(cfg.selectors_file).is_file():
        add("warn", f"PO_SELECTORS_FILE introuvable : {cfg.selectors_file}")
    if cfg.allow_real:
        add("warn", f"ALLOW_REAL=1 : le mode RÉEL est autorisé (plafond de mise ${cfg.max_real_stake:g})")
    return out


async def network_checks(cfg) -> List[Check]:
    out: List[Check] = []
    try:
        from telegram import Bot
        bot = Bot(cfg.telegram_token)
        me = await asyncio.wait_for(bot.get_me(), 20)
        out.append(Check("ok", f"Telegram : connecté en tant que @{me.username}"))
        if cfg.channel_id:
            try:
                m = await asyncio.wait_for(bot.get_chat_member(cfg.channel_id, me.id), 20)
                st = getattr(m, "status", "")
                out.append(Check("ok" if st in ("administrator", "creator") else "warn",
                                 f"Canal {cfg.channel_id} : le bot y est « {st} »"
                                 + ("" if st in ("administrator", "creator") else " (il doit être administrateur pour vérifier les abonnés)")))
            except Exception as e:
                out.append(Check("warn", f"Canal {cfg.channel_id} inaccessible : {str(e)[:100]}"))
    except Exception as e:
        out.append(Check("error", f"Telegram : {e.__class__.__name__} — {str(e)[:120]} (token invalide ou pas d'accès Internet)"))
    if (cfg.auto_signup and cfg.po_register_url) or cfg.partners_email:
        try:
            import po_web
            ok, det = await asyncio.wait_for(po_web.WebAutomation(cfg, cfg.data_dir).diagnose(), 60)
            out.append(Check("ok" if ok else "warn", f"Navigateur Playwright : {det}"))
        except Exception as e:
            out.append(Check("warn", f"Navigateur Playwright : {str(e)[:120]}"))
    return out


def report(checks: List[Check]) -> str:
    return "\n".join(f"{ICON[c.level]} {c.text}" for c in checks)


def has_error(checks: List[Check]) -> bool:
    return any(c.level == "error" for c in checks)
