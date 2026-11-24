#!/bin/bash
set -euo pipefail

# Ubuntu EC2 deployment script for PBQMS. Run as root (sudo).
#
# Optional environment:
#   REPO_URL, BRANCH      — where to clone from
#   DOMAIN                — public hostname; when set, HTTPS is configured with
#                           Let's Encrypt (certbot) and HTTP redirects to HTTPS
#   CERTBOT_EMAIL         — contact address for Let's Encrypt (with DOMAIN)

APP_DIR=/home/ubuntu/pbqms
REPO_URL=${REPO_URL:-https://github.com/your-org/pbqms.git}
BRANCH=${BRANCH:-main}
DOMAIN=${DOMAIN:-}
CERTBOT_EMAIL=${CERTBOT_EMAIL:-}

apt-get update
apt-get install -y python3-pip python3-venv nginx git curl rsync acl build-essential pkg-config

if [ ! -d "$APP_DIR" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  cd "$APP_DIR"
  # Fail loudly: a silently failed update would leave old code running.
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
fi

cd "$APP_DIR"
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p static/qrcodes
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created $APP_DIR/.env from the example: edit it before starting the service." >&2
fi

# Rate-limit zones (http context).
cat > /etc/nginx/conf.d/pbqms-limits.conf <<'EOF'
limit_req_zone $binary_remote_addr zone=pbqms_login:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=pbqms_public:10m rate=10r/m;
EOF

SERVER_NAME=${DOMAIN:-_}
cat > /etc/nginx/sites-available/pbqms <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${SERVER_NAME};
    client_max_body_size 1m;

    # The app trusts exactly one proxy (TRUSTED_PROXY_COUNT=1): this one.
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;

    location /static/ {
        alias /home/ubuntu/pbqms/static/;
        expires 30d;
    }

    location = /api/auth/login {
        limit_req zone=pbqms_login burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8000;
    }

    location ~ ^/api/queue/(register|appointments)\$ {
        limit_req zone=pbqms_public burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8000;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# nginx (www-data) serves /static/ straight from the app directory. Ubuntu
# creates home directories as 750, so grant www-data traverse-only access
# (no listing) along the path; otherwise every static file returns 403.
setfacl -m u:www-data:x /home/ubuntu "$APP_DIR"
chmod -R o+rX "$APP_DIR/static"

ln -sf /etc/nginx/sites-available/pbqms /etc/nginx/sites-enabled/pbqms
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

if [ -n "$DOMAIN" ]; then
  apt-get install -y certbot python3-certbot-nginx
  if [ -n "$CERTBOT_EMAIL" ]; then
    certbot --nginx --non-interactive --agree-tos --redirect -m "$CERTBOT_EMAIL" -d "$DOMAIN"
  else
    certbot --nginx --non-interactive --agree-tos --redirect --register-unsafely-without-email -d "$DOMAIN"
  fi
else
  echo "WARNING: DOMAIN not set; serving plain HTTP. Staff passwords and session" >&2
  echo "cookies cross the network unencrypted. Re-run with DOMAIN=... to enable HTTPS." >&2
fi

cat > /etc/systemd/system/pbqms.service <<'EOF'
[Unit]
Description=PBQMS Flask Application
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/pbqms
Environment=PATH=/home/ubuntu/pbqms/venv/bin
Environment=APP_ENV=production
Environment=TZ=Asia/Colombo
ExecStart=/home/ubuntu/pbqms/venv/bin/gunicorn --config /home/ubuntu/pbqms/gunicorn.conf.py wsgi:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Periodic jobs (replace the old, never-scheduled Celery tasks).
cat > /etc/cron.d/pbqms <<'EOF'
CRON_TZ=Asia/Colombo
SHELL=/bin/bash
PATH=/home/ubuntu/pbqms/venv/bin:/usr/bin:/bin
APP_ENV=production
FLASK_APP=wsgi.py
# Close the previous day's queues shortly after midnight.
5 0 * * * ubuntu cd /home/ubuntu/pbqms && flask close-day >> /var/log/pbqms-cron.log 2>&1
# Re-send recently failed SMS.
*/5 * * * * ubuntu cd /home/ubuntu/pbqms && flask retry-sms >> /var/log/pbqms-cron.log 2>&1
EOF
touch /var/log/pbqms-cron.log
chown ubuntu:ubuntu /var/log/pbqms-cron.log

# Keep application logs (which can contain masked phone numbers) for 30 days.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/pbqms-retention.conf <<'EOF'
[Journal]
MaxRetentionSec=30day
EOF
systemctl restart systemd-journald

systemctl daemon-reload
systemctl enable pbqms
chown -R ubuntu:ubuntu "$APP_DIR"
systemctl restart pbqms

echo "EC2 deployment setup completed."
echo "Next steps:"
echo "  sudo nano /home/ubuntu/pbqms/.env      # SECRET_KEY, DATABASE_URL, BASE_URL, SMS settings"
echo "  sudo systemctl status pbqms"
echo "  curl -fsS http://127.0.0.1:8000/healthz"
echo "  sudo journalctl -u pbqms -f"
