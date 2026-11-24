"""Compare a freshly migrated database with the SQLAlchemy models."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from app import create_app
from app.extensions import db


def schema_drift(database_uri):
    """Run every migration on an EMPTY database at `database_uri` and return
    the differences from the models (an empty list means no drift)."""
    app = create_app("testing", SQLALCHEMY_DATABASE_URI=database_uri, SKIP_CREATE_ALL=True)
    with app.app_context():
        upgrade()
        with db.engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": False})
            return compare_metadata(context, db.metadata)
