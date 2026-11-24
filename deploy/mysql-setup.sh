#!/bin/bash
# One-time: install MySQL on this EC2 host, create the pbqms database and
# user, write DATABASE_URL into the app's .env, and schedule nightly backups.
# Run as root:  sudo bash deploy/mysql-setup.sh
set -euo pipefail

APP_DIR=/home/ubuntu/pbqms
ENV_FILE=$APP_DIR/.env
DB_NAME=pbqms
DB_USER=pbqms_user
BACKUP_DIR=/var/backups/pbqms

if [ ! -f "$ENV_FILE" ]; then
  echo "$ENV_FILE not found; run deploy/ec2-setup.sh first." >&2
  exit 1
fi
if grep -Eq '^DATABASE_URL=.+' "$ENV_FILE"; then
  echo "DATABASE_URL is already set in $ENV_FILE; not changing it." >&2
  echo "Remove that line first if you really want a new local MySQL database." >&2
  exit 1
fi

apt-get update
apt-get install -y mysql-server openssl
systemctl enable --now mysql   # Ubuntu's MySQL listens on 127.0.0.1 only

DB_PASSWORD=$(openssl rand -hex 24)
mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

sed -i '/^DATABASE_URL=$/d' "$ENV_FILE"
printf 'DATABASE_URL=mysql+pymysql://%s:%s@localhost/%s\n' "$DB_USER" "$DB_PASSWORD" "$DB_NAME" >> "$ENV_FILE"
chown ubuntu:ubuntu "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Nightly dump at 01:30 Colombo time, kept 14 days. These live on the same
# disk as the database: also enable EBS snapshots (AWS Data Lifecycle Manager).
install -d -m 700 "$BACKUP_DIR"
cat > /etc/cron.d/pbqms-backup <<EOF
CRON_TZ=Asia/Colombo
SHELL=/bin/bash
30 1 * * * root mysqldump --single-transaction --routines ${DB_NAME} | gzip > ${BACKUP_DIR}/${DB_NAME}-\$(date +\\%F).sql.gz && find ${BACKUP_DIR} -name '*.sql.gz' -mtime +14 -delete
EOF

echo "MySQL ready; DATABASE_URL written to $ENV_FILE."
echo "Next (as ubuntu, in $APP_DIR):"
echo "  FLASK_APP=wsgi.py venv/bin/flask db upgrade"
echo "  FLASK_APP=wsgi.py venv/bin/flask seed-db"
echo "  FLASK_APP=wsgi.py venv/bin/flask create-admin"
echo "  sudo systemctl restart pbqms"
