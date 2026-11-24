import sqlite3

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_socketio import SocketIO
from sqlalchemy import event
from sqlalchemy.engine import Engine

db            = SQLAlchemy()
migrate       = Migrate()
login_manager = LoginManager()
socketio      = SocketIO()

login_manager.login_view    = "auth_view.login_page"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


@event.listens_for(Engine, "connect")
def _sqlite_enforce_foreign_keys(dbapi_conn, _record):
    """SQLite ignores foreign keys unless asked; enforce them like MySQL does
    so integrity bugs show up in development and tests too."""
    if isinstance(dbapi_conn, sqlite3.Connection):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@login_manager.user_loader
def load_user(user_id):
    from app.models import User
    user = db.session.get(User, int(user_id))
    # Deactivated accounts must lose access immediately, including sessions
    # and "remember me" cookies that were issued before deactivation.
    if user is None or not user.is_active:
        return None
    return user
