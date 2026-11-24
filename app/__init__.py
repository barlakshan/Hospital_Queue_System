import logging
import os
import secrets

import click
from dotenv import load_dotenv

# Load .env before any config is read
load_dotenv()

from flask import Flask, jsonify, redirect, request, url_for
from app.extensions import db, migrate, login_manager, socketio
from app.config import config_by_name, config_name_from_env

# Import models eagerly so db.create_all() and CLI seeding include the full schema.
from app import models  # noqa: F401

logger = logging.getLogger(__name__)

# Third-party assets are pinned with SRI hashes in the templates; this policy
# limits where scripts, styles and connections may come from.
# TODO(P3-7 follow-up): move inline <script> blocks into static files, then
# drop 'unsafe-inline' from script-src.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "font-src 'self' data: https://cdn.jsdelivr.net",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
])

# Endpoints a user who must change their password may still reach.
_PASSWORD_CHANGE_EXEMPT = {
    "static", "auth_view.change_password", "auth_view.logout",
    "auth_view.login_page", "auth_api.login", "auth_api.login_page",
    "auth_api.change_password", "auth_api.me", "healthz",
}


def initialize_database(app):
    """Create tables automatically in tests; use Alembic for real databases."""
    if app.config.get("TESTING") and not app.config.get("SKIP_CREATE_ALL"):
        with app.app_context():
            db.create_all()


def home_endpoint_for(user):
    from app.models import UserRole
    if user.role == UserRole.ADMIN:
        return "admin_view.dashboard"
    if user.role == UserRole.BRANCH_MANAGER:
        return "manager_view.dashboard"
    return "officer.dashboard"


def _register_security_hooks(app):
    from datetime import timedelta
    from urllib.parse import urlparse
    from flask import abort
    from flask_login import current_user

    @app.before_request
    def reject_cross_site_writes():
        """CSRF defence: state-changing requests must come from our own origin.

        Browsers always send Origin (or at least Referer) on cross-site POSTs,
        so a mismatch means a forged request. Non-browser clients that send
        neither header (curl, tests, server-to-server) are unaffected.
        """
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        source = request.headers.get("Origin") or request.headers.get("Referer")
        if not source:
            return None
        if urlparse(source).netloc != request.host:
            abort(403)
        return None

    @app.before_request
    def staff_session_housekeeping():
        if not current_user.is_authenticated:
            return None

        # Record activity (at most once a minute) for "online now".
        from app.utils.timeutil import as_utc, utcnow
        now = utcnow()
        last = as_utc(current_user.last_seen)
        if last is None or now - last > timedelta(seconds=60):
            current_user.last_seen = now
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Accounts whose password was set by an admin must choose their own
        # before using the staff pages.
        if (current_user.must_change_password
                and request.endpoint not in _PASSWORD_CHANGE_EXEMPT
                and request.method == "GET"
                and not request.path.startswith("/api/")):
            return redirect(url_for("auth_view.change_password"))
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if response.mimetype == "text/html":
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if request.path.startswith(("/api/", "/admin", "/manager", "/officer", "/auth")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _check_production_config(app):
    secret_key = app.config.get("SECRET_KEY")
    if not secret_key or len(secret_key) < 32 or "change" in secret_key.lower():
        raise RuntimeError("SECRET_KEY must be set to a strong random value (32+ chars) in production")
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("DATABASE_URL must be set in production")
    from app.services.sms_service import SmsService
    provider = app.config.get("SMS_PROVIDER")
    if provider not in SmsService.KNOWN_PROVIDERS:
        raise RuntimeError(
            f"SMS_PROVIDER {provider!r} is not one of: {', '.join(SmsService.KNOWN_PROVIDERS)}")
    if provider == "mock":
        logger.warning("SMS_PROVIDER is mock: patients will NOT receive any SMS. "
                       "Set SMS_PROVIDER=textlk (and TEXTLK_*) in .env.")
    if provider == "textlk" and not app.config.get("TEXTLK_API_TOKEN"):
        raise RuntimeError("SMS_PROVIDER is textlk but TEXTLK_API_TOKEN is not set")
    if provider == "textlk" and not os.environ.get("TEXTLK_SENDER_ID"):
        logger.warning(
            "TEXTLK_SENDER_ID is not set; using %r. Text.lk rejects messages "
            "from sender IDs that are not registered to your account.",
            app.config.get("TEXTLK_SENDER_ID"),
        )


def create_app(config_name=None, **overrides):
    """Application factory. Keyword overrides are applied on top of the
    selected config before any extension is initialised (used by tests)."""
    if config_name is None:
        config_name = config_name_from_env()

    # Normalize: "1" or "true" sometimes set by tools
    if config_name not in config_by_name:
        config_name = "development"

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
    )
    app.config.from_object(config_by_name[config_name])
    app.config.update(overrides)
    app.config["CONFIG_NAME"] = config_name

    # Without a handler only WARNING+ reaches stderr (and journalctl), so
    # INFO lines such as "SMS sent" were invisible on the server.
    if not app.testing and not logging.getLogger().handlers:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    if config_name == "production":
        _check_production_config(app)
    elif not app.config.get("SECRET_KEY"):
        # Never run sessions on an empty key; use an ephemeral random one.
        app.config["SECRET_KEY"] = secrets.token_hex(32)

    # Behind nginx every request comes from 127.0.0.1; trust exactly as many
    # proxies as are configured so client IPs (login throttling, audit log)
    # and the request scheme are correct, and cannot be spoofed otherwise.
    proxies = int(app.config.get("TRUSTED_PROXY_COUNT") or 0)
    if proxies > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    initialize_database(app)
    # Same-origin only by default; set BASE_URL to allow the public hostname.
    base_url = (app.config.get("BASE_URL") or "").rstrip("/")
    socketio.init_app(
        app,
        cors_allowed_origins=[base_url] if base_url else None,
        async_mode="threading",
        logger=False,
        engineio_logger=False,
    )

    _register_security_hooks(app)

    # Register API blueprints
    from app.api import auth_bp, queue_bp, admin_bp, manager_bp
    for bp in [auth_bp, queue_bp, admin_bp, manager_bp]:
        app.register_blueprint(bp)

    # Register view blueprints
    from app.views import customer_bp, display_bp, officer_bp, admin_view_bp, manager_view_bp, auth_view_bp
    for bp in [auth_view_bp, customer_bp, display_bp, officer_bp, admin_view_bp, manager_view_bp]:
        app.register_blueprint(bp)

    from flask_login import current_user

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for(home_endpoint_for(current_user)))
        return redirect(url_for("auth_view.login_page"))

    @app.route("/healthz")
    def healthz():
        """Liveness + database check for deploy scripts and load balancers."""
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify({"status": "ok"})
        except Exception:
            logger.exception("Health check failed")
            db.session.rollback()
            return jsonify({"status": "error"}), 503

    _register_template_helpers(app)
    register_cli(app)
    return app


def _register_template_helpers(app):
    from app.models import ROLE_LABELS
    from app.utils.timeutil import to_local

    @app.template_filter("number_format")
    def number_format_filter(value):
        """Format a number with thousands separators."""
        try:
            return "{:,}".format(int(value))
        except (ValueError, TypeError):
            return value

    @app.template_filter("localtime")
    def localtime_filter(value, fmt="%Y-%m-%d %H:%M"):
        """Render a stored UTC datetime in the hospital's time zone."""
        if value is None:
            return ""
        return to_local(value).strftime(fmt)

    @app.template_filter("role_label")
    def role_label_filter(role):
        return ROLE_LABELS.get(role, getattr(role, "value", str(role)).replace("_", " ").title())


def _delete_tokens(token_ids):
    """Delete tokens and every row that references them (FK-safe)."""
    from app.models import AuditLog, QueueToken, SatisfactionRating, SmsLog
    if not token_ids:
        return 0
    for model in (SmsLog, SatisfactionRating):
        model.query.filter(model.token_id.in_(token_ids)).delete(synchronize_session=False)
    AuditLog.query.filter(AuditLog.token_id.in_(token_ids)).update(
        {"token_id": None}, synchronize_session=False)
    return QueueToken.query.filter(QueueToken.id.in_(token_ids)).delete(
        synchronize_session=False)


def register_cli(app):
    hospital_services = [
        ("G", "General OPD", "G", 10, ["opd"]),
        ("S", "Specialist Consultation", "S", 15,
         ["opd", "dentist", "cardiologists", "dermatologists"]),
        ("L", "Laboratory", "L", 8, ["pharmacy"]),
        ("R", "Radiology", "R", 12, ["pharmacy"]),
        ("P", "Pharmacy", "P", 6, ["pharmacy"]),
        ("B", "Billing / Cashier", "B", 5, ["opd", "pharmacy"]),
    ]

    @app.cli.command("seed-db")
    def seed_db():
        """Seed hospital services (run `flask db upgrade` first)."""
        from app.models import ServiceType

        with app.app_context():
            db.session.remove()
            if app.config.get("TESTING"):
                db.create_all()
                db.session.commit()
            else:
                # create_all() on a real database builds tables from the current
                # models without recording an Alembic revision, leaving a schema
                # that `flask db upgrade` can neither stamp nor migrate.
                inspector = db.inspect(db.engine)
                migrated = "alembic_version" in inspector.get_table_names() and \
                    db.session.execute(db.text("SELECT 1 FROM alembic_version")).first()
                if not migrated:
                    raise click.ClickException(
                        "The database schema is not migrated. Run `flask db upgrade` first."
                    )

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
        from app.utils.validators import validate_password
        email = email.strip().lower()
        error = validate_password(password)
        if error:
            raise click.ClickException(error)
        if User.query.filter((User.email == email) | (User.employee_id == employee_id)).first():
            click.echo(f"User {email} or employee ID {employee_id} already exists.")
            return
        u = User(
            employee_id=employee_id, full_name=name,
            email=email, role=UserRole.ADMIN, branch_id=None,
        )
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Admin user '{name}' ({email}) created successfully.")

    @app.cli.command("reset-password")
    @click.option("--email",    prompt="Email")
    @click.option("--password", prompt="New password", hide_input=True, confirmation_prompt=True)
    def reset_password(email, password):
        """Set a user's password and re-activate the account (server access only)."""
        from app.models import User
        from app.utils.validators import validate_password
        from app.services.audit_service import AuditService
        email = email.strip().lower()
        error = validate_password(password)
        if error:
            raise click.ClickException(error)
        user = User.query.filter_by(email=email).first()
        if not user:
            raise click.ClickException(f"No user with email {email}.")
        user.set_password(password)
        user.is_active = True
        user.must_change_password = False
        db.session.commit()
        AuditService.log(user_id=user.id, action="password_reset_cli",
                         payload={"email": email})
        click.echo(f"Password reset for {user.full_name} ({email}, {user.role.value}).")

    @app.cli.command("list-admins")
    def list_admins():
        """List admin accounts, to find the email to reset."""
        from app.models import User, UserRole
        admins = User.query.filter_by(role=UserRole.ADMIN).order_by(User.id).all()
        if not admins:
            click.echo("No admin accounts. Run `flask create-admin`.")
        for u in admins:
            state = "active" if u.is_active else "INACTIVE"
            click.echo(f"{u.email}\t{u.full_name}\t{state}")

    @app.cli.command("reset-queue")
    @click.option("--branch", default=None, help="Branch code (omit for all)")
    @click.option("--confirm", is_flag=True, help="Confirm deleting today's tokens.")
    def reset_queue(branch, confirm):
        """Delete today's queue (testing only)."""
        from app.models import Branch, QueueToken
        from app.utils.timeutil import local_today
        if not confirm:
            raise click.ClickException("This deletes today's tokens. Re-run with --confirm.")
        q = QueueToken.query.filter_by(queue_date=local_today())
        if branch:
            b = Branch.query.filter_by(code=branch.upper()).first()
            if not b:
                raise click.ClickException(f"Unknown branch {branch}")
            q = q.filter_by(branch_id=b.id)
        token_ids = [token_id for (token_id,) in q.with_entities(QueueToken.id)]
        count = _delete_tokens(token_ids)
        db.session.commit()
        click.echo(f"Deleted {count} tokens for today.")

    @app.cli.command("close-day")
    def close_day():
        """Close previous days' queues. Run daily from cron after midnight."""
        from app.models import AuditLog, QueueToken, TokenStatus
        from app.utils.timeutil import local_today, utcnow
        cutoff = local_today()
        skipped = QueueToken.query.filter(
            QueueToken.queue_date < cutoff,
            QueueToken.status == TokenStatus.WAITING,
        ).update({"status": TokenStatus.SKIPPED}, synchronize_session=False)
        no_show = QueueToken.query.filter(
            QueueToken.queue_date < cutoff,
            QueueToken.status.in_([TokenStatus.CALLED, TokenStatus.SERVING]),
        ).update({"status": TokenStatus.NO_SHOW, "completed_at": utcnow()},
                 synchronize_session=False)
        if skipped or no_show:
            db.session.add(AuditLog(action="close_day", payload={
                "before": cutoff.isoformat(), "skipped": skipped, "no_show": no_show}))
        db.session.commit()
        click.echo(f"Closed queues before {cutoff}: {skipped} skipped, {no_show} no-show.")

    @app.cli.command("retry-sms")
    @click.option("--max-age", default=10, show_default=True,
                  help="Only retry failures newer than this many minutes.")
    def retry_sms(max_age):
        """Re-send recently failed SMS. Run every few minutes from cron."""
        from app.services.sms_service import SmsService
        attempted, sent = SmsService.retry_failed(max_age_minutes=max_age)
        click.echo(f"Retried {attempted} failed SMS; {sent} sent.")

    @app.cli.command("send-test-sms")
    @click.argument("mobile")
    def send_test_sms(mobile):
        """Send one SMS now and print the gateway's answer (diagnostics)."""
        from app.services.sms_service import SmsService
        from app.utils.validators import mask_mobile, normalise_mobile
        provider = app.config.get("SMS_PROVIDER")
        click.echo(f"SMS_PROVIDER={provider}")
        if provider == "textlk":
            click.echo(f"TEXTLK_SENDER_ID={app.config.get('TEXTLK_SENDER_ID')}")
            click.echo("TEXTLK_API_TOKEN is " +
                       ("set" if app.config.get("TEXTLK_API_TOKEN") else "NOT set"))
        if provider == "mock":
            click.echo("Provider is mock: nothing will be sent. Set SMS_PROVIDER in .env.")
        try:
            number = normalise_mobile(mobile)
            result = SmsService._dispatch(
                number, f"{SmsService.BRAND}: SMS test message. Please ignore.")
        except Exception as exc:
            raise click.ClickException(f"SMS FAILED: {exc}")
        click.echo(f"SMS accepted for {mask_mobile(number)}: {result}")

    @app.cli.command("purge-patients")
    @click.option("--older-than", "older_than", type=int, required=True,
                  help="Delete patients with no visit in this many days.")
    @click.option("--confirm", is_flag=True)
    def purge_patients(older_than, confirm):
        """Delete patient records past the retention period."""
        from datetime import timedelta
        from sqlalchemy import func
        from app.models import AppointmentRequest, AuditLog, Customer, QueueToken
        from app.utils.timeutil import local_today
        if older_than < 30:
            raise click.ClickException("--older-than must be at least 30 days")
        cutoff = local_today() - timedelta(days=older_than)
        last_visit = (db.session.query(QueueToken.customer_id,
                                       func.max(QueueToken.queue_date).label("last"))
                      .group_by(QueueToken.customer_id).subquery())
        candidates = (db.session.query(Customer.id)
                      .outerjoin(last_visit, last_visit.c.customer_id == Customer.id)
                      .filter(db.or_(last_visit.c.last < cutoff,
                                     db.and_(last_visit.c.last.is_(None),
                                             func.date(Customer.created_at) < cutoff)))
                      .all())
        customer_ids = [cid for (cid,) in candidates]
        if not confirm:
            click.echo(f"{len(customer_ids)} patient(s) would be deleted. Re-run with --confirm.")
            return
        if customer_ids:
            token_ids = [t for (t,) in db.session.query(QueueToken.id)
                         .filter(QueueToken.customer_id.in_(customer_ids))]
            _delete_tokens(token_ids)
            AppointmentRequest.query.filter(
                AppointmentRequest.customer_id.in_(customer_ids)
            ).update({"customer_id": None}, synchronize_session=False)
            Customer.query.filter(Customer.id.in_(customer_ids)).delete(
                synchronize_session=False)
            db.session.add(AuditLog(action="purge_patients", payload={
                "older_than_days": older_than, "deleted": len(customer_ids)}))
        db.session.commit()
        click.echo(f"Deleted {len(customer_ids)} patient(s) with no visit since {cutoff}.")

    @app.cli.command("clean-data")
    @click.option("--confirm", is_flag=True, help="Confirm permanent deletion of non-admin data.")
    def clean_data(confirm):
        """Delete all application data except Admin accounts; locations are removed too."""
        if not confirm:
            raise click.ClickException(
                "This permanently deletes all non-admin data. Re-run with --confirm."
            )

        from app.models import (
            AppointmentRequest, AuditLog, Branch, Counter, Customer,
            QueueToken, SatisfactionRating, ServiceType, SmsLog, User, UserRole,
        )
        from app.models.user import user_services

        with app.app_context():
            retained_admins = User.query.filter_by(role=UserRole.ADMIN).count()
            # Children before parents so foreign keys hold on every database.
            for model in (
                SatisfactionRating, SmsLog, AuditLog, AppointmentRequest,
                QueueToken, Counter, Customer,
            ):
                db.session.query(model).delete(synchronize_session=False)

            db.session.execute(user_services.delete())
            User.query.filter_by(role=UserRole.ADMIN).update(
                {"branch_id": None}, synchronize_session=False
            )
            User.query.filter(User.role != UserRole.ADMIN).delete(
                synchronize_session=False
            )
            db.session.query(ServiceType).delete(synchronize_session=False)
            db.session.query(Branch).delete(synchronize_session=False)
            db.session.commit()
            click.echo(
                f"Data cleaned. Kept {retained_admins} Admin account(s); "
                "removed all other records, including locations and services."
            )

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
