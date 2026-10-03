#!/usr/bin/env bash
# Installation sur un VPS Ubuntu 22.04 / 24.04 (à lancer en root depuis le dossier du projet) :  sudo bash deploy/install_ubuntu.sh
set -euo pipefail
APP=/opt/ltbot
apt-get update && apt-get install -y python3 python3-venv python3-pip rsync
id ltbot >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin ltbot
mkdir -p "$APP" && rsync -a --exclude venv --exclude data --exclude .env ./ "$APP"/
[ -f "$APP/.env" ] || cp "$APP/.env.example" "$APP/.env"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --upgrade pip
"$APP/venv/bin/pip" install -r "$APP/requirements.txt"
"$APP/venv/bin/python" -m playwright install --with-deps chromium
chown -R ltbot:ltbot "$APP" && chmod 600 "$APP/.env"
cp "$APP/deploy/ltbot.service" /etc/systemd/system/ltbot.service
systemctl daemon-reload
echo
echo "1) Éditez $APP/.env (TELEGRAM_TOKEN, ADMIN_IDS, liens…)"
echo "2) Contrôle :  sudo -u ltbot $APP/venv/bin/python $APP/main.py --check"
echo "3) Démarrage : systemctl enable --now ltbot && journalctl -u ltbot -f"
