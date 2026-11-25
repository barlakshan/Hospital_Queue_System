"""Run the app for one UAT test, in its own process.

Usage: python uat_server.py <database-uri>
Prints the chosen port on stdout, then serves until killed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402


def uat_app(database_uri):
    """The testing config (mock SMS), pinned so a local .env cannot change it."""
    return create_app(
        "testing",
        SQLALCHEMY_DATABASE_URI=database_uri,
        BASE_URL="",
        ALLOW_SELF_DECLARED_PRIORITY=False,
        MAX_TOKENS_PER_MOBILE_PER_DAY=3,
    )


if __name__ == "__main__":
    server = make_server("127.0.0.1", 0, uat_app(sys.argv[1]), threaded=True)
    print(server.server_port, flush=True)
    server.serve_forever()
