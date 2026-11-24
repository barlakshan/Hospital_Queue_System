# AWS EC2 deployment guide for PBQMS

## 1. Prepare the EC2 instance
Use an Ubuntu 22.04+ instance with at least:
- 2 vCPUs
- 4 GB RAM
- 20 GB storage
- Security group allowing TCP 80, 443, and 22

## 2. Install dependencies on the server
```bash
sudo apt update
sudo apt install -y git
```

## 3. Run the setup script
```bash
sudo bash -c 'REPO_URL=https://github.com/your-org/pbqms.git BRANCH=main DOMAIN=queue.example.com CERTBOT_EMAIL=ops@example.com bash -s' < deploy/ec2-setup.sh
```

With `DOMAIN` set, the script obtains a Let's Encrypt certificate and redirects HTTP to HTTPS. Without it the site is served over plain HTTP, which exposes staff passwords and sessions: only acceptable for a short test. The script also installs:

- `/etc/cron.d/pbqms` — `flask close-day` at 00:05 and `flask retry-sms` every 5 minutes (Asia/Colombo time);
- nginx rate limits for sign-in and public registration;
- a 30-day journald retention period.

If you are deploying from the current workspace, copy the repo to the server first and run:
```bash
sudo bash deploy/ec2-setup.sh
```

## 4. Configure environment variables
Edit the app environment file:
```bash
sudo nano /home/ubuntu/pbqms/.env
```

Recommended production values:
```env
APP_ENV=production
SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=mysql+pymysql://user:password@host:3306/dbname
BASE_URL=https://queue.example.com
TRUSTED_PROXY_COUNT=1
SMS_PROVIDER=textlk
TEXTLK_API_TOKEN=<token>
TEXTLK_SENDER_ID=<sender ID registered with Text.lk>
```

The app refuses to start in production without `SECRET_KEY` and `DATABASE_URL`; it no longer falls back to SQLite.

## 5. Initialize the database
```bash
cd /home/ubuntu/pbqms
source venv/bin/activate
flask db upgrade
flask seed-db
flask create-admin
```

## 6. Verify the deployment
```bash
sudo systemctl status pbqms
curl -fsS http://127.0.0.1:8000/healthz
```

## 7. Enable GitHub Actions deployment
Every push to `main` runs the tests and MySQL migration checks on GitHub-hosted runners. If they pass, the `deploy` job connects to this instance over SSH, syncs the tested commit into `/home/ubuntu/pbqms` with rsync (keeping `.env`, `venv/` and `static/qrcodes/`), then runs `deploy/remote-deploy.sh`: `pip install`, `flask db upgrade`, restart `pbqms`, and a `/healthz` check.

1. **Create a deploy key that is used only for CI** on your own computer, not the server:
   ```bash
   ssh-keygen -t ed25519 -N "" -C "github-actions-pbqms" -f pbqms_deploy_key
   ```
2. **Authorize it on the server** by adding the contents of `pbqms_deploy_key.pub` as a new line in `/home/ubuntu/.ssh/authorized_keys`.
3. **Get the server's host key** so CI can detect an impostor host. From your computer:
   ```bash
   ssh-keyscan -t ed25519 <EC2_PUBLIC_IP>
   ```
   On the server, `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` should show the same fingerprint as `ssh-keygen -lf <(ssh-keyscan -t ed25519 <EC2_PUBLIC_IP>)`.
4. **Add the secrets in GitHub**: open **Settings → Environments → New environment** and name it `production`. Then add these environment secrets:
   | Secret | Value |
   |---|---|
   | `EC2_HOST` | the instance's public IP (use an Elastic IP so it doesn't change) |
   | `EC2_SSH_KEY` | the full contents of `pbqms_deploy_key` (the private key) |
   | `EC2_KNOWN_HOSTS` | the line printed by `ssh-keyscan` in step 3 |

   Then delete the local `pbqms_deploy_key` files, or keep them only in a password manager.
5. **Security group:** GitHub-hosted runners use changing IP ranges, so port 22 must accept connections from anywhere. Only key-based logins are allowed on EC2 Ubuntu by default; keep it that way.
6. **Server prerequisites:** `sudo apt install -y rsync`, and `/home/ubuntu/pbqms` must be owned by `ubuntu`. On EC2 Ubuntu the `ubuntu` user already has passwordless sudo, which is what the `systemctl restart pbqms` step needs.
7. Push to `main`, or run **CI/CD** from the Actions tab. To require a manual approval before each production deploy, add yourself as a *required reviewer* on the `production` environment.

If an older self-hosted runner is installed on this instance, remove it: `cd ~/actions-runner && sudo ./svc.sh stop && sudo ./svc.sh uninstall`, then delete the runner under **Settings → Actions → Runners**.

### Running over plain HTTP (no domain yet)
Production cookies are marked Secure, so browsers drop them on `http://` and login fails. Until HTTPS is configured, add these lines to `.env`:
```env
BASE_URL=http://<EC2_PUBLIC_IP>
COOKIE_SECURE=false
```
Staff passwords and sessions then cross the network unencrypted. Remove `COOKIE_SECURE=false` as soon as HTTPS is on.

### Local MySQL
`sudo bash deploy/mysql-setup.sh` installs MySQL (listening on localhost only), creates the `pbqms` database and user with a random password, writes `DATABASE_URL` into `.env`, and schedules nightly `mysqldump` backups to `/var/backups/pbqms` (kept for 14 days).

## 8. HTTPS
Run the setup script with `DOMAIN=...` (step 3). To add HTTPS later, re-run it with `DOMAIN` set, or:
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx --redirect -d your-domain.com
```

## Notes
- The app is served through Gunicorn and Nginx.
- Socket.IO traffic is proxied through Nginx.
- Gunicorn runs one worker with 50 threads. Don't raise `workers`: Socket.IO live updates need a single process unless a message queue and sticky sessions are added.
