"""Point d'entrée : python main.py        (démarre le bot)
                   python main.py --check (contrôle l'installation sans démarrer le bot)"""
import asyncio
import logging
import logging.handlers
import re
import sys

from config import load_config
from version import __version__


class SafeFormatter(logging.Formatter):
    """Masque le token du bot, les cookies de session et les SSID dans TOUS les journaux (messages et tracebacks)."""

    def __init__(self, fmt, token: str = ""):
        super().__init__(fmt)
        self.token = token

    def format(self, record):
        s = super().format(record)
        if self.token:
            s = s.replace(self.token, "***TOKEN***")
        s = re.sub(r'("session"\s*:\s*")[^"]{6,}', r"\1***", s)
        return re.sub(r"(ci_session=)[^\s;]+", r"\1***", s)


def setup_logging(cfg, level_name: str) -> None:
    import os
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [logging.StreamHandler()]
    try:
        log_dir = cfg.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(log_dir / "bot.log", maxBytes=5_000_000, backupCount=3,
                                                             encoding="utf-8"))
    except OSError:
        pass
    for h in handlers:
        h.setFormatter(SafeFormatter(fmt, cfg.telegram_token))
    logging.basicConfig(level=level, handlers=handlers, force=True)
    for noisy in ("httpx", "httpcore", "telegram.ext.ExtBot", "apscheduler"):   # ces logs contiennent l'URL avec le token
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    import os
    import preflight
    cfg = load_config()
    import runtime_settings
    import texts
    import visuals
    runtime_settings.apply(cfg, runtime_settings.load(cfg.data_dir))          # réglages faits depuis Telegram : priment sur .env
    texts.configure(cfg.data_dir)                                            # textes modifiés : data/textes.json
    visuals.configure(cfg.assets_dir)                                        # images : ./assets (ou ASSETS_DIR)
    visuals.set_custom_dir(cfg.data_dir / "banners")                         # images ajoutées depuis Telegram
    setup_logging(cfg, os.getenv("LOG_LEVEL", "INFO"))
    log = logging.getLogger("bot")
    checks = preflight.static_checks(cfg)
    if "--check" in sys.argv:
        checks += asyncio.run(preflight.network_checks(cfg))
        print(f"LTBot {__version__} — contrôle de l'installation\n")
        print(preflight.report(checks))
        sys.exit(1 if preflight.has_error(checks) else 0)
    if preflight.has_error(checks):
        print(f"LTBot {__version__} — démarrage impossible :\n\n" + preflight.report([c for c in checks if c.level != "ok"]),
              file=sys.stderr)
        sys.exit(1)
    for c in checks:
        if c.level == "warn":
            log.warning(c.text)
    from bot.app import build_application
    from store import Store
    log.info("LTBot %s · courtier=%s · réel=%s · vérification=%s · admins=%d", __version__, cfg.broker,
             "autorisé" if cfg.allow_real else "verrouillé", cfg.verify_mode, len(cfg.admin_ids))

    app = build_application(cfg, Store(cfg.data_dir))
    # SIGINT et SIGTERM (systemd, Docker, Ctrl+C) déclenchent un arrêt propre : sessions stoppées, navigateurs fermés
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])
    log.info("arrêt propre terminé")


if __name__ == "__main__":
    main()
