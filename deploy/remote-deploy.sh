#!/bin/bash
# Runs ON the EC2 host as `ubuntu`, invoked over SSH by the CI/CD workflow
# after it has synced the tested commit into $APP_DIR.
set -Eeuo pipefail

APP_DIR=/home/ubuntu/pbqms
cd "$APP_DIR"

echo "==> Installing Python dependencies"
venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

mkdir -p static/qrcodes

echo "==> Applying database migrations"
FLASK_APP=wsgi.py APP_ENV=production venv/bin/flask db upgrade

echo "==> Restarting pbqms"
sudo -n /usr/bin/systemctl restart pbqms

echo "==> Health check"
# /healthz also checks the database; a login redirect is not proof of health.
if ! curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 \
        --max-time 5 http://127.0.0.1:8000/healthz; then
  echo "Health check failed. Recent service log:" >&2
  sudo -n /usr/bin/journalctl -u pbqms -n 50 --no-pager >&2 || true
  exit 1
fi
echo
echo "==> Deployed $(cat .deployed-commit 2>/dev/null || echo unknown)"
