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
sudo bash -c 'REPO_URL=https://github.com/your-org/pbqms.git BRANCH=main bash -s' < deploy/ec2-setup.sh
```

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
FLASK_ENV=production
SECRET_KEY=replace-with-a-long-random-string
DATABASE_URL=sqlite:////home/ubuntu/pbqms/pbqms_dev.sqlite3
BASE_URL=http://YOUR_EC2_PUBLIC_IP
SMS_PROVIDER=mock
```

For production MySQL, use a MySQL URI such as:
```env
DATABASE_URL=mysql+pymysql://user:password@host:3306/dbname
```

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
curl http://127.0.0.1:8000/
```

## 7. Enable GitHub Actions deployment
The workflow runs tests on GitHub-hosted Ubuntu, then deploys pushes to `main` on a self-hosted runner installed on this EC2 instance.

1. Install and register a GitHub Actions self-hosted runner for this repository on the EC2 instance. Run the runner service as `ubuntu`; the workflow expects its default `self-hosted`, `linux`, and `x64` labels.
2. Install `rsync` and make the existing checkout writable by the runner:
   ```bash
   sudo apt install -y rsync
   sudo chown -R ubuntu:ubuntu /home/ubuntu/pbqms
   ```
3. Keep the production `.env` at `/home/ubuntu/pbqms/.env`. The workflow deliberately excludes it and the SQLite database and QR-code data from source sync.
4. Allow the runner to restart only this application service without a password. Create `/etc/sudoers.d/pbqms-deploy` with:
   ```text
   ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart pbqms
   ```
   Validate the file with `sudo visudo -cf /etc/sudoers.d/pbqms-deploy`.
5. Push to `main` or run **CI/CD** from the repository's Actions tab. The deployment applies database migrations, restarts `pbqms`, and checks the local HTTP endpoint.

## 8. Optional: HTTPS with Nginx and Let's Encrypt
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## Notes
- The app is served through Gunicorn and Nginx.
- Socket.IO traffic is proxied through Nginx.
- For production, replace the SQLite fallback with MySQL or another managed database.
