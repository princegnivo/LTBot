import logging
import sys

from bot.app import build_application
from config import load_config
from store import Store


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    cfg = load_config()
    if not cfg.telegram_token:
        sys.exit("TELEGRAM_TOKEN manquant (voir .env.example).")
    if not cfg.allowed_user_ids:
        sys.exit("ALLOWED_USER_IDS vide : par sécurité le bot refuse tout le monde. "
                 "Envoyez /id au bot pour connaître votre ID, puis renseignez-le dans .env.")
    logging.getLogger("bot").info("Démarrage · courtier=%s · réel=%s", cfg.broker, "autorisé" if cfg.allow_real else "verrouillé")
    build_application(cfg, Store(cfg.data_dir)).run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
