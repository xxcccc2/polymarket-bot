#!/usr/bin/env bash
set -euo pipefail
cd /opt/polymarket-bot
python3 -m venv venv
venv/bin/pip install -r requirements-vps.txt
mkdir -p data
install -m 644 deploy/polymarket-ml.service /etc/systemd/system/polymarket-ml.service
install -m 644 deploy/polymarket-spread.service /etc/systemd/system/polymarket-spread.service
install -m 644 deploy/polymarket-telegram.service /etc/systemd/system/polymarket-telegram.service
install -m 644 deploy/polymarket-daily.service /etc/systemd/system/polymarket-daily.service
install -m 644 deploy/polymarket-daily.timer /etc/systemd/system/polymarket-daily.timer
systemctl daemon-reload
echo 'Services installed; add Telegram credentials, then enable polymarket-telegram and polymarket-daily.timer.'
