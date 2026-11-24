"""Regression tests for the fixes in docs/audit (Phases 0-4)."""
import io
import logging
from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from app import create_app
from app.extensions import db
from app.models import (
    AppointmentRequest, AuditLog, Branch, Counter, Customer, QueueToken,
    SatisfactionRating, ServiceType, SmsLog, TokenStatus, User, UserRole,
)
from app.models.user import user_services
from app.utils.timeutil import local_today
from app.utils.validators import normalise_mobile


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


def seed(app):
    """One location, one service, an OPD officer, a manager and an admin."""
    with app.app_context():
        branch = Branch(code="COL", name="Colombo")
        other = Branch(code="KAN", name="Kandy")
        service = ServiceType(code="G", name="General OPD", prefix="G",
                              avg_service_min=10, allowed_roles=["opd"])
        db.session.add_all([branch, other, service])
        db.session.commit()
        users = {}
        for email, role, branch_id in (
            ("opd@x.com", UserRole.OPD, branch.id),
            ("opd2@x.com", UserRole.OPD, branch.id),
            ("mgr@x.com", UserRole.BRANCH_MANAGER, branch.id),
            ("admin@x.com", UserRole.ADMIN, None),
        ):
            user = User(employee_id=email[:10], full_name=email, email=email, role=role,
                        branch_id=branch_id,
                        assigned_services=[service] if role == UserRole.OPD else [])
            user.set_password("secret123")
            db.session.add(user)
            users[email] = user
        db.session.commit()
        return {"branch": branch.id, "other": other.id, "service": service.id,
                **{k: u.id for k, u in users.items()}}


def login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    assert r.status_code == 200, r.get_json()


def register(client, ids, name="Nimal Perera", mobile="0771234567", **extra):
    return client.post("/api/queue/register", json={
        "branch_id": ids["branch"], "service_type_id": ids["service"],
        "full_name": name, "mobile": mobile, **extra})


# ── Phase 0 ────────────────────────────────────────────────────────────────
def test_production_refuses_to_start_without_database_url(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app("production", SQLALCHEMY_DATABASE_URI=None, SECRET_KEY="x" * 40)


def test_testing_config_has_a_secret_key_without_env():
    assert create_app("testing").config["SECRET_KEY"]


# ── Phase 1 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, expected", [
    ("0771234567", "+94771234567"),
    ("771234567", "+94771234567"),
    ("94771234567", "+94771234567"),
    ("+94771234567", "+94771234567"),
    ("0094771234567", "+94771234567"),
    ("077 123-4567", "+94771234567"),
    ("+447911123456", "+447911123456"),
])
def test_phone_normalisation(raw, expected):
    assert normalise_mobile(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "0771234", "+0771234567", "abc", "+9477123456"])
def test_phone_normalisation_rejects_bad_numbers(raw):
    with pytest.raises(ValueError):
        normalise_mobile(raw)


def test_registration_never_renames_an_existing_patient(app, client):
    ids = seed(app)
    assert register(client, ids, name="Nimal Perera").status_code == 201
    r = register(client, ids, name="Someone Else", mobile="+94 77 123 4567",
                 service_type_id=ids["service"])
    # Same mobile + same service = same open token (idempotent).
    assert r.status_code == 200 and r.get_json()["existing"] is True
    with app.app_context():
        assert Customer.query.count() == 1
        assert Customer.query.one().full_name == "Nimal Perera"


def test_visit_name_is_stored_on_the_token(app, client):
    ids = seed(app)
    with app.app_context():
        svc2 = ServiceType(code="P", name="Pharmacy", prefix="P", allowed_roles=["opd"])
        db.session.add(svc2)
        db.session.commit()
        svc2_id = svc2.id
    register(client, ids, name="Parent Name")
    r = register(client, ids, name="Child Name", service_type_id=svc2_id)
    assert r.status_code == 201
    with app.app_context():
        token = db.session.get(QueueToken, r.get_json()["token_id"])
        assert token.patient_name == "Child Name"
        assert token.customer.full_name == "Parent Name"


def test_token_number_collision_is_retried(app, client, monkeypatch):
    ids = seed(app)
    first = register(client, ids, mobile="0771111111").get_json()
    from app.services.queue_service import QueueService
    real = QueueService.generate_token_number
    calls = []

    def stale_number(branch_id, service_type_id):
        calls.append(1)
        # First attempt reuses the number another request just took.
        return first["token_number"] if len(calls) == 1 else real(branch_id, service_type_id)

    monkeypatch.setattr(QueueService, "generate_token_number", staticmethod(stale_number))
    r = register(client, ids, mobile="0772222222")
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["token_number"] != first["token_number"]
    assert len(calls) == 2


def test_queue_day_and_display_times_use_hospital_time_zone(app):
    with app.app_context():
        from zoneinfo import ZoneInfo
        assert local_today() == datetime.now(ZoneInfo("Asia/Colombo")).date()
        render = app.jinja_env.from_string("{{ t|localtime('%H:%M') }}").render
        assert render(t=datetime(2026, 1, 1, 3, 0)) == "08:30"   # stored UTC -> +05:30


def test_completing_a_visit_does_not_overwrite_the_patient_summary(app, client):
    ids = seed(app)
    token_id = register(client, ids).get_json()["token_id"]
    with app.app_context():
        customer = Customer.query.one()
        customer.condition_summary = "Curated: diabetic"
        db.session.commit()
    login(client, "opd@x.com")
    client.post("/api/queue/call-next", json={})
    r = client.post(f"/api/queue/token/{token_id}/complete",
                    json={"condition_summary": "Collected medicine"})
    assert r.status_code == 200
    with app.app_context():
        assert Customer.query.one().condition_summary == "Curated: diabetic"
        assert db.session.get(QueueToken, token_id).condition_summary == "Collected medicine"


def test_service_with_queued_patients_cannot_be_deactivated(app, client):
    ids = seed(app)
    register(client, ids)
    login(client, "admin@x.com")
    client.post(f"/admin/services/{ids['service']}/status", data={"action": "deactivate"})
    with app.app_context():
        assert db.session.get(ServiceType, ids["service"]).is_active is True


def test_call_next_refuses_while_a_patient_is_open(app, client):
    ids = seed(app)
    register(client, ids, mobile="0771111111")
    register(client, ids, mobile="0772222222")
    login(client, "opd@x.com")
    first = client.post("/api/queue/call-next", json={})
    assert first.status_code == 200
    second = client.post("/api/queue/call-next", json={})
    assert second.status_code == 409
    assert first.get_json()["token_number"] in second.get_json()["error"]
    current = client.get("/api/queue/current").get_json()
    assert current["token_id"] == first.get_json()["token_id"]


def test_close_day_closes_previous_days(app):
    ids = seed(app)
    with app.app_context():
        customer = Customer(full_name="A", mobile="+94770000001")
        db.session.add(customer)
        db.session.commit()
        yesterday = local_today() - timedelta(days=1)
        for number, status in (("G001", TokenStatus.WAITING), ("G002", TokenStatus.CALLED),
                               ("G003", TokenStatus.SERVING)):
            db.session.add(QueueToken(branch_id=ids["branch"], customer_id=customer.id,
                                      service_type_id=ids["service"], token_number=number,
                                      status=status, queue_date=yesterday))
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["close-day"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        statuses = sorted(t.status.name for t in QueueToken.query.all())
        assert statuses == ["NO_SHOW", "NO_SHOW", "SKIPPED"]


def test_reset_queue_and_clean_data_respect_foreign_keys(app, client):
    ids = seed(app)
    token_id = register(client, ids).get_json()["token_id"]
    with app.app_context():
        token = db.session.get(QueueToken, token_id)
        token.status = TokenStatus.COMPLETED
        db.session.add(SatisfactionRating(token_id=token_id, rating=5))
        db.session.commit()
        assert SmsLog.query.filter_by(token_id=token_id).count() == 1
    runner = app.test_cli_runner()
    result = runner.invoke(args=["reset-queue", "--confirm"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        assert QueueToken.query.count() == 0
        assert SmsLog.query.count() == 0
    result = runner.invoke(args=["clean-data", "--confirm"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        assert db.session.execute(user_services.select()).fetchall() == []
        assert User.query.filter(User.role != UserRole.ADMIN).count() == 0


def test_migrations_match_models(tmp_path):
    from tests.migration_drift import schema_drift
    assert schema_drift(f"sqlite:///{tmp_path / 'migrated.db'}") == []


def test_started_consultation_records_duration(app, client):
    ids = seed(app)
    token_id = register(client, ids).get_json()["token_id"]
    login(client, "opd@x.com")
    assert client.post("/api/queue/call-next", json={}).get_json()["status"] == "called"
    assert client.post(f"/api/queue/token/{token_id}/start").status_code == 200
    with app.app_context():
        token = db.session.get(QueueToken, token_id)
        token.served_at = datetime.now(timezone.utc) - timedelta(minutes=12)
        db.session.commit()
    client.post(f"/api/queue/token/{token_id}/complete", json={})
    with app.app_context():
        assert db.session.get(QueueToken, token_id).service_duration_minutes == 12


def test_daily_token_cap_per_mobile(app, client):
    ids = seed(app)
    with app.app_context():
        for code in ("A", "B", "C"):
            db.session.add(ServiceType(code=code, name=f"Svc {code}", prefix=code,
                                       allowed_roles=["opd"]))
        db.session.commit()
        extra = [s.id for s in ServiceType.query.filter(ServiceType.code.in_(list("ABC")))]
    assert register(client, ids).status_code == 201
    assert register(client, ids, service_type_id=extra[0]).status_code == 201
    assert register(client, ids, service_type_id=extra[1]).status_code == 201
    r = register(client, ids, service_type_id=extra[2])
    assert r.status_code == 400 and "already has" in r.get_json()["error"]


def test_appointment_request_lifecycle(app, client):
    ids = seed(app)
    body = {"branch_id": ids["branch"], "service_type_id": ids["service"],
            "full_name": "Asha", "mobile": "077 123 4567"}
    r = client.post("/api/queue/appointments", json=body)
    assert r.status_code == 201 and r.get_json()["status"] == "pending"
    again = client.post("/api/queue/appointments", json=body)
    assert again.status_code == 200 and again.get_json()["existing"] is True
    request_id = r.get_json()["request_id"]
    with app.app_context():
        assert db.session.get(AppointmentRequest, request_id).mobile == "+94771234567"

    login(client, "opd@x.com")
    r = client.post(f"/api/queue/appointments/{request_id}/status", json={"status": "scheduled"})
    assert r.status_code == 200
    with app.app_context():
        appointment = db.session.get(AppointmentRequest, request_id)
        assert appointment.status == "scheduled"
        assert appointment.handled_by == ids["opd@x.com"]


def test_audit_records_login_failures_and_rejected_actions(app, client):
    ids = seed(app)
    client.post("/api/auth/login", json={"email": "opd@x.com", "password": "wrong"})
    login(client, "opd@x.com")
    client.post("/api/queue/token/999/complete", json={})
    with app.app_context():
        actions = {log.action: log for log in AuditLog.query.all()}
        assert "login_failed" in actions and "login_success" in actions
        rejected = actions["complete_service"]
        assert rejected.payload["status"] == 404
        assert rejected.payload["outcome"] == "rejected"
        assert rejected.user_id == ids["opd@x.com"]


def test_user_edit_is_audited_with_before_and_after(app, client):
    ids = seed(app)
    login(client, "admin@x.com")
    client.post(f"/admin/users/{ids['opd@x.com']}/edit", data={
        "employee_id": "opd@x.com", "full_name": "Renamed", "email": "opd@x.com",
        "role": "PHARMACY", "branch_id": str(ids["branch"]),
        "service_ids": [str(ids["service"])], "is_active": "1"})
    with app.app_context():
        log = AuditLog.query.filter_by(action="user_updated_detail").one()
        assert log.payload["changes"]["role"] == ["opd", "pharmacy"]


# ── Phase 2 ────────────────────────────────────────────────────────────────
def test_admin_can_create_and_edit_a_location_manager(app, client):
    ids = seed(app)
    login(client, "admin@x.com")
    r = client.post("/api/admin/users", json={
        "employee_id": "M2", "full_name": "Manager Two", "email": "m2@x.com",
        "password": "secret123", "role": "BRANCH_MANAGER", "branch_id": ids["branch"]})
    assert r.status_code == 201, r.get_json()
    form = client.get(f"/admin/users/{ids['mgr@x.com']}/edit").data
    assert b'value="BRANCH_MANAGER" selected' in form
    assert b"Location Manager" in form


def test_staff_roles_require_a_location(app, client):
    seed(app)
    login(client, "admin@x.com")
    r = client.post("/api/admin/users", json={
        "employee_id": "N1", "full_name": "No Location", "email": "n1@x.com",
        "password": "secret123", "role": "OPD", "service_ids": []})
    assert r.status_code == 400
    assert "location" in r.get_json()["error"]


def test_display_never_falls_back_to_another_location(app, client):
    seed(app)
    assert client.get("/display/COL").status_code == 200
    assert client.get("/display/NOPE").status_code == 404
    with app.app_context():
        Branch.query.filter_by(code="COL").one().is_active = False
        db.session.commit()
    assert client.get("/display/COL").status_code == 404


def test_display_payload_is_the_same_for_push_and_poll(app, client):
    ids = seed(app)
    register(client, ids, mobile="0771111111")
    register(client, ids, mobile="0772222222")
    with app.app_context():
        from app.services.notification_service import build_display_state
        branch = db.session.get(Branch, ids["branch"])
        pushed = build_display_state(branch)
    assert client.get("/api/queue/display/COL").get_json() == pushed


def test_admin_exports_work(app, client):
    seed(app)
    login(client, "admin@x.com")
    dashboard = client.get("/admin/dashboard").data
    assert b"/api/admin/analytics/export" not in dashboard
    assert b"format=csv" not in dashboard
    r = client.get("/admin/analytics/export")
    assert r.status_code == 200
    workbook = load_workbook(io.BytesIO(r.data))
    assert "By location" in workbook.sheetnames


def test_admin_can_open_manager_pages_for_a_chosen_location(app, client):
    ids = seed(app)
    register(client, ids)
    login(client, "admin@x.com")
    assert client.get("/manager/dashboard").status_code == 302
    page = client.get(f"/manager/dashboard?branch_id={ids['branch']}")
    assert page.status_code == 200 and b"Colombo" in page.data
    assert client.get("/manager/customers").status_code == 200   # remembered


def test_admin_customer_list_is_paginated_and_searchable(app, client):
    seed(app)
    with app.app_context():
        for i in range(60):
            db.session.add(Customer(full_name=f"Patient {i:02d}", mobile=f"+9477000{i:04d}"))
        db.session.commit()
    login(client, "admin@x.com")
    page = client.get("/admin/customers").data.decode()
    assert page.count("<strong>Patient ") == 50
    found = client.get("/admin/customers?q=Patient 07").data.decode()
    assert found.count("<strong>Patient ") == 1


@pytest.mark.parametrize("code", ["ADMIN", "DISPLAY", "REGISTER"])
def test_reserved_location_codes_are_rejected(app, client, code):
    seed(app)
    login(client, "admin@x.com")
    r = client.post("/api/admin/branches", json={"name": "X", "code": code})
    assert r.status_code == 400


# ── Phase 3 ────────────────────────────────────────────────────────────────
def test_proxy_headers_are_trusted_only_when_configured():
    seen = {}
    for count in (0, 1):
        app = create_app("testing", TRUSTED_PROXY_COUNT=count)

        @app.get("/_ip")
        def _ip():
            from flask import request
            return request.remote_addr

        with app.test_client() as c:
            seen[count] = c.get("/_ip", headers={"X-Forwarded-For": "203.0.113.5"},
                                environ_base={"REMOTE_ADDR": "127.0.0.1"}).data
    assert seen == {0: b"127.0.0.1", 1: b"203.0.113.5"}


def test_logout_requires_post_and_password_change_is_enforced(app, client):
    seed(app)
    with app.app_context():
        user = User.query.filter_by(email="opd@x.com").one()
        user.must_change_password = True
        db.session.commit()
    client.post("/api/auth/login", data={"email": "opd@x.com", "password": "secret123"})
    r = client.get("/officer/dashboard")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/password")
    r = client.post("/auth/password", data={"current_password": "secret123",
                                             "new_password": "n3w-secret!",
                                             "confirm_password": "n3w-secret!"})
    assert r.status_code == 302
    assert client.get("/officer/dashboard").status_code == 200
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout").status_code == 302
    assert client.get("/api/auth/me").status_code in (302, 401)


def test_login_without_remember_me_sets_no_remember_cookie(app, client):
    seed(app)
    r = client.post("/api/auth/login", json={"email": "opd@x.com", "password": "secret123"})
    assert "remember_token" not in r.headers.get("Set-Cookie", "")


def test_html_pages_send_a_content_security_policy_and_pin_cdn_assets(app, client):
    seed(app)
    r = client.get("/auth/login")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    page = client.get("/register/COL").data.decode()
    assert "cdn.jsdelivr.net" in page and 'integrity="sha384-' in page


def test_logs_never_contain_full_phone_numbers(app, client, caplog):
    ids = seed(app)
    with caplog.at_level(logging.DEBUG):
        register(client, ids, mobile="0771234567")
    assert "771234567" not in caplog.text


# ── Phase 4 ────────────────────────────────────────────────────────────────
def test_dashboards_show_no_invented_numbers(app, client):
    seed(app)
    login(client, "opd@x.com")
    officer = client.get("/officer/dashboard").data.decode()
    assert "↑ 3 vs yesterday" not in officer and ">8m<" not in officer
    client.post("/auth/logout")
    login(client, "mgr@x.com")
    manager = client.get("/manager/dashboard").data.decode()
    assert ">18m<" not in manager and "8% vs avg" not in manager
    client.post("/auth/logout")
    login(client, "admin@x.com")
    admin = client.get("/admin/dashboard").data.decode()
    assert "500% vs yesterday" not in admin


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_counter_cannot_be_assigned_to_staff_of_another_location(app):
    ids = seed(app)
    with app.app_context():
        db.session.add(Counter(branch_id=ids["other"], label="X1", user_id=ids["opd@x.com"]))
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
