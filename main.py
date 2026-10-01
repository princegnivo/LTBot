import logging
import sys

from bot.app import build_application
from config import load_config
from store import Store


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    for noisy in ("httpx", "httpcore"):          # ces logs contiennent l'URL avec le token du bot
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_config()
    if not cfg.telegram_token:
        sys.exit("TELEGRAM_TOKEN manquant (voir .env.example).")
    if not cfg.admin_ids:
        sys.exit("ADMIN_IDS vide : il faut au moins un administrateur. "
                 "Envoyez /id au bot pour connaître votre ID, puis renseignez-le dans .env (ADMIN_IDS=...).")
    logging.getLogger("bot").info("Démarrage · courtier=%s · réel=%s · admins=%d", cfg.broker, "autorisé" if cfg.allow_real else "verrouillé", len(cfg.admin_ids))
    build_application(cfg, Store(cfg.data_dir)).run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
