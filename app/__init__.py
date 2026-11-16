import os
import click
from dotenv import load_dotenv

# Load .env before any config is read
load_dotenv()

from flask import Flask, redirect, url_for
from app.extensions import db, migrate, login_manager, socketio
from app.config import config_by_name

# Import models eagerly so db.create_all() and CLI seeding include the full schema.
from app import models  # noqa: F401


def initialize_database(app):
    """Create tables automatically in tests; use Alembic for real databases."""
    if app.config.get("TESTING"):
        with app.app_context():
            db.create_all()


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    # Normalize: "1" or "true" sometimes set by tools
    if config_name not in config_by_name:
        config_name = "development"

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
    )
    app.config.from_object(config_by_name[config_name])

    if app.config.get("ENV") == "production":
        secret_key = app.config.get("SECRET_KEY")
        if not secret_key or len(secret_key) < 24 or "change" in secret_key.lower() or "dev" in secret_key.lower():
            raise RuntimeError("SECRET_KEY must be set to a strong random value in production")

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    initialize_database(app)
    socketio.init_app(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False,
        engineio_logger=False,
    )

    # Register API blueprints
    from app.api import auth_bp, queue_bp, admin_bp, manager_bp
    for bp in [auth_bp, queue_bp, admin_bp, manager_bp]:
        app.register_blueprint(bp)

    # Register view blueprints
    from app.views import customer_bp, display_bp, officer_bp, admin_view_bp, manager_view_bp, auth_view_bp
    for bp in [auth_view_bp, customer_bp, display_bp, officer_bp, admin_view_bp, manager_view_bp]:
        app.register_blueprint(bp)

    # Root redirect
    from flask_login import current_user

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            role = current_user.role.value
            if role == "admin":
                return redirect(url_for("admin_view.dashboard"))
            elif role == "branch_manager":
                return redirect(url_for("manager_view.dashboard"))
            else:
                return redirect(url_for("officer.dashboard"))
        return redirect(url_for("auth_api.login_page"))

    # Register Jinja2 filters
    @app.template_filter('number_format')
    def number_format_filter(value):
        """Format a number with thousands separators."""
        try:
            return "{:,}".format(int(value))
        except (ValueError, TypeError):
            return value

    register_cli(app)
    return app


def register_cli(app):
    hospital_services = [
        ("G", "General OPD", "G", 10, ["lobby_officer"]),
        ("S", "Specialist Consultation", "S", 15,
         ["lobby_officer", "loan_officer", "specialist_doctor"]),
        ("L", "Laboratory", "L", 8, ["loan_officer"]),
        ("R", "Radiology", "R", 12, ["loan_officer"]),
        ("P", "Pharmacy", "P", 6, ["lobby_officer", "loan_officer"]),
        ("B", "Billing / Cashier", "B", 5, ["lobby_officer", "loan_officer"]),
    ]

    @app.cli.command("seed-db")
    def seed_db():
        """Seed hospital services and initialize the schema if needed."""
        from app.models import ServiceType

        with app.app_context():
            db.session.remove()
            db.create_all()
            db.session.commit()

            added = 0
            for code, name, prefix, avg_service_min, allowed_roles in hospital_services:
                if not ServiceType.query.filter_by(code=code).first():
                    db.session.add(ServiceType(
                        code=code,
                        name=name,
                        prefix=prefix,
                        avg_service_min=avg_service_min,
                        allowed_roles=allowed_roles,
                    ))
                    added += 1

            ServiceType.query.filter(
                ServiceType.code.in_(["AO", "AC", "FD", "LN", "OT"])
            ).update({"is_active": False}, synchronize_session=False)
            db.session.commit()
            click.echo(f"Service types seeded ({added} added).")

    @app.cli.command("create-admin")
    @click.option("--employee-id", prompt="Employee ID")
    @click.option("--name",        prompt="Full name")
    @click.option("--email",       prompt="Email")
    @click.option("--password",    prompt="Password", hide_input=True, confirmation_prompt=True)
    def create_admin(employee_id, name, email, password):
        """Create an admin user."""
        from app.models import User, UserRole
        if User.query.filter_by(email=email).first():
            click.echo(f"User {email} already exists.")
            return
        u = User(
            employee_id=employee_id, full_name=name,
            email=email, role=UserRole.ADMIN, branch_id=None,
        )
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Admin user '{name}' ({email}) created successfully.")

    @app.cli.command("reset-queue")
    @click.option("--branch", default=None, help="Branch code (omit for all)")
    def reset_queue(branch):
        """Reset today's queue for testing."""
        from app.models import QueueToken
        from datetime import date
        q = QueueToken.query.filter_by(queue_date=date.today())
        if branch:
            from app.models import Branch
            b = Branch.query.filter_by(code=branch.upper()).first()
            if b:
                q = q.filter_by(branch_id=b.id)
        count = q.count()
        q.delete()
        db.session.commit()
        click.echo(f"Deleted {count} tokens for today.")

    @app.cli.command("generate-qr")
    @click.option("--branch", default=None, help="Branch code")
    @click.option("--all", "all_branches", is_flag=True, help="All active branches")
    def generate_qr(branch, all_branches):
        """Generate QR code PNG(s) for branches."""
        from app.models import Branch
        from app.utils.qr_generator import generate_branch_qr
        if all_branches:
            branches = Branch.query.filter_by(is_active=True).all()
        elif branch:
            branches = [Branch.query.filter_by(code=branch.upper()).first()]
            branches = [b for b in branches if b]
        else:
            click.echo("Specify --branch CODE or --all")
            return
        for b in branches:
            path = generate_branch_qr(b)
            click.echo(f"QR generated: {path}")
