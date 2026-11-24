import os
import tempfile
from datetime import timedelta

from dotenv import load_dotenv

# Load .env every time config is imported (safe to call multiple times)
load_dotenv()


def _normalize_sms_provider(value):
    """Map the many spellings of SMS_PROVIDER onto one provider name.

    This is the single normaliser; SmsService reads the already-normalised
    value from app config.
    """
    if not value:
        return "mock"

    provider = str(value).strip().lower()

    if provider.startswith(("http://", "https://")):
        if "text.lk" in provider or "textlk" in provider:
            return "textlk"
        if "dialog" in provider:
            return "dialog"
        if "mobitel" in provider:
            return "mobitel"
        if "twilio" in provider:
            return "twilio"
        return "mock"

    if provider in {"textlk", "text.lk", "text-lk", "text_lk", "text"}:
        return "textlk"
    if provider in {"mock", "none", "off", "disabled"}:
        return "mock"

    return provider


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def config_name_from_env(default="development"):
    """APP_ENV selects the config; FLASK_ENV is still read for one release."""
    return os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or default


# Zero-config local database: a SQLite file next to the project root.
# Used only in development when DATABASE_URL isn't set. Production refuses
# to start without DATABASE_URL (see create_app).
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SQLITE_URI = "sqlite:///" + os.path.join(_BASE_DIR, "pbqms_dev.sqlite3")


class BaseConfig:
    SECRET_KEY                     = os.environ.get("SECRET_KEY")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO                = False
    # All "today" calculations and every time shown to staff use this zone.
    APP_TIMEZONE                   = os.environ.get("APP_TIMEZONE", "Asia/Colombo")
    SMS_PROVIDER                   = _normalize_sms_provider(os.environ.get("SMS_PROVIDER", "mock"))
    # Send SMS in the request thread instead of a background thread (tests).
    SMS_SEND_INLINE                = False
    # Patients are asked to be at the hospital this many minutes before
    # their estimated turn.
    SMS_ARRIVE_EARLY_MIN           = int(os.environ.get("SMS_ARRIVE_EARLY_MIN", "20"))
    DIALOG_USER                   = os.environ.get("DIALOG_USER", "")
    DIALOG_PASS                    = os.environ.get("DIALOG_PASS", "")
    DIALOG_SENDER_ID               = os.environ.get("DIALOG_SENDER_ID", "MELSTAHOSP")
    MOBITEL_TOKEN                  = os.environ.get("MOBITEL_TOKEN", "")
    MOBITEL_SENDER_ID              = os.environ.get("MOBITEL_SENDER_ID", "MELSTAHOSP")
    TEXTLK_API_TOKEN               = os.environ.get("TEXTLK_API_TOKEN", "")
    TEXTLK_SENDER_ID               = os.environ.get("TEXTLK_SENDER_ID", "MELSTAHOSP")
    TWILIO_SID                     = os.environ.get("TWILIO_SID", "")
    TWILIO_TOKEN                   = os.environ.get("TWILIO_TOKEN", "")
    TWILIO_FROM                    = os.environ.get("TWILIO_FROM", "")
    BASE_URL                       = os.environ.get("BASE_URL", "")
    # Number of reverse proxies in front of the app whose X-Forwarded-*
    # headers are trusted. 0 = not behind a proxy (never trust the headers).
    TRUSTED_PROXY_COUNT            = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
    MAX_CONTENT_LENGTH             = 1 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY        = True
    SESSION_COOKIE_SAMESITE        = "Lax"
    REMEMBER_COOKIE_HTTPONLY       = True
    REMEMBER_COOKIE_SAMESITE       = "Lax"
    REMEMBER_COOKIE_DURATION       = timedelta(days=7)
    # A signed-in session expires after one shift unless "keep me signed in".
    PERMANENT_SESSION_LIFETIME     = timedelta(hours=8)
    # Login throttling: hard limit per client IP, soft delay per account.
    LOGIN_IP_MAX_FAILURES          = 20
    LOGIN_IP_LOCK_SECONDS          = 300
    LOGIN_FAILURE_DELAY_MAX        = 5     # seconds of delay per account
    # Public registration rules.
    ALLOW_SELF_DECLARED_PRIORITY   = _env_bool("ALLOW_SELF_DECLARED_PRIORITY", False)
    MAX_TOKENS_PER_MOBILE_PER_DAY  = int(os.environ.get("MAX_TOKENS_PER_MOBILE_PER_DAY", "3"))
    MAX_APPOINTMENTS_PER_MOBILE_PER_DAY = 3


class DevelopmentConfig(BaseConfig):
    DEBUG                      = True
    # Falls back to a local SQLite file so the app works out of the box
    # with no MySQL server required. Set DATABASE_URL to opt into MySQL.
    SQLALCHEMY_DATABASE_URI    = os.environ.get("DATABASE_URL", DEFAULT_SQLITE_URI)


class ProductionConfig(BaseConfig):
    DEBUG                      = False
    # No fallback: create_app() refuses to start without DATABASE_URL so a
    # missing setting can never silently put production on SQLite.
    SQLALCHEMY_DATABASE_URI    = os.environ.get("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS  = {"pool_pre_ping": True, "pool_recycle": 280}
    TRUSTED_PROXY_COUNT        = int(os.environ.get("TRUSTED_PROXY_COUNT", "1"))
    # Browsers drop Secure cookies on plain-HTTP sites, so login only works
    # over HTTPS. COOKIE_SECURE=false is solely for a temporary HTTP-on-IP
    # trial; it sends staff session cookies unencrypted.
    SESSION_COOKIE_SECURE      = _env_bool("COOKIE_SECURE", True)
    REMEMBER_COOKIE_SECURE     = _env_bool("COOKIE_SECURE", True)


class TestingConfig(BaseConfig):
    TESTING                    = True
    SECRET_KEY                 = "testing-only-secret-key-not-for-production-use"
    # Never reach a real SMS gateway from tests, whatever .env says.
    SMS_PROVIDER               = "mock"
    SMS_SEND_INLINE            = True
    TEXTLK_API_TOKEN           = ""
    DIALOG_PASS                = ""
    MOBITEL_TOKEN              = ""
    TWILIO_TOKEN               = ""
    TRUSTED_PROXY_COUNT        = 0
    LOGIN_FAILURE_DELAY_MAX    = 0
    # One database file per process: parallel runs can't corrupt each other
    # and nothing is written into the repository.
    SQLALCHEMY_DATABASE_URI    = os.environ.get(
        "TEST_DATABASE_URL",
        "sqlite:///" + os.path.join(tempfile.gettempdir(), f"pbqms_test_{os.getpid()}.sqlite3"),
    )
    WTF_CSRF_ENABLED           = False


config_by_name = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "testing":     TestingConfig,
    "default":     DevelopmentConfig,
}
