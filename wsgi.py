import os
from dotenv import load_dotenv

# Load .env FIRST before anything else imports config
load_dotenv()

from app import create_app
from app.config import config_name_from_env
from app.extensions import socketio

# APP_ENV (or the older FLASK_ENV) selects the config; development by default.
env = config_name_from_env()
app = create_app(env)

if __name__ == "__main__":
    # Local development server only. Production runs gunicorn
    # (see gunicorn.conf.py, Dockerfile and deploy/ec2-setup.sh).
    if env == "production":
        raise SystemExit("Use gunicorn in production: gunicorn --config gunicorn.conf.py wsgi:app")
    socketio.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5000)),
        debug=(env == "development"),
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
