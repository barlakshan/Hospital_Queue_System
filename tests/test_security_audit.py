"""Regression tests for issues found in the security audit."""
import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Branch, Customer, QueueToken, ServiceType, TokenStatus, User, UserRole,
)


@pytest.fixture()
def client():
    app = create_app("testing")
    with app.app_context():
        db.drop_all()
        db.create_all()
    with app.test_client() as c:
        yield c
    with app.app_context():
        db.drop_all()


def _user(email, role, branch_id=None, services=()):
    u = User(employee_id=email[:15], full_name=email, email=email, role=role,
             branch_id=branch_id, assigned_services=list(services))
    u.set_password("secret123")
    db.session.add(u)
    return u


def _seed():
    a, b = Branch(code="AAA", name="A"), Branch(code="BBB", name="B")
    svc = ServiceType(code="G", name="General", prefix="G", avg_service_min=5,
                      allowed_roles=["opd"])
    db.session.add_all([a, b, svc])
    db.session.commit()
    _user("opd_a@x.com", UserRole.OPD, a.id, [svc])
    _user("opd_b@x.com", UserRole.OPD, b.id, [svc])
    _user("mgr_a@x.com", UserRole.BRANCH_MANAGER, a.id)
    _user("admin@x.com", UserRole.ADMIN)
    cust = Customer(full_name="<img src=x onerror=alert(1)>", mobile="+94771234567")
    db.session.add(cust)
    db.session.commit()
    tok = QueueToken(branch_id=a.id, customer_id=cust.id, service_type_id=svc.id,
                     token_number="G001", status=TokenStatus.CALLED)
    db.session.add(tok)
    db.session.commit()
    return a.id, b.id, tok.id


def _login(client, email):
    return client.post("/api/auth/login", json={"email": email, "password": "secret123"})


def test_staff_cannot_act_on_other_branch_token(client):
    with client.application.app_context():
        _, _, token_id = _seed()
    _login(client, "opd_b@x.com")
    for action in ("start", "complete", "no-show", "priority"):
        r = client.post(f"/api/queue/token/{token_id}/{action}",
                        json={"priority_reason": "ELDERLY"})
        assert r.status_code == 403, action


def test_same_branch_staff_can_still_complete(client):
    with client.application.app_context():
        _, _, token_id = _seed()
    _login(client, "opd_a@x.com")
    assert client.post(f"/api/queue/token/{token_id}/complete", json={}).status_code == 200
    # a completed token cannot be re-opened / no-showed
    assert client.post(f"/api/queue/token/{token_id}/no-show", json={}).status_code == 409


def _ref(app, token_id):
    with app.app_context():
        return db.session.get(QueueToken, token_id).public_ref


def test_cannot_rate_unfinished_visit(client):
    with client.application.app_context():
        _, _, token_id = _seed()
    ref = _ref(client.application, token_id)
    r = client.post(f"/api/queue/token/{ref}/rate", json={"rating": 5})
    assert r.status_code == 409


def test_public_token_urls_reject_sequential_ids(client):
    with client.application.app_context():
        _, _, token_id = _seed()
    assert client.get(f"/register/token/{token_id}").status_code == 404
    assert client.get(f"/api/queue/token/{token_id}/status").status_code == 404
    assert client.post(f"/api/queue/token/{token_id}/rate", json={"rating": 5}).status_code == 404


def test_deactivated_user_session_is_revoked(client):
    with client.application.app_context():
        _seed()
    _login(client, "opd_a@x.com")
    assert client.get("/api/auth/me").status_code == 200
    with client.application.app_context():
        User.query.filter_by(email="opd_a@x.com").first().is_active = False
        db.session.commit()
    assert client.get("/api/auth/me").status_code in (302, 401)


def test_admin_qr_endpoint_does_not_crash_and_hides_paths(client):
    with client.application.app_context():
        a, _, _ = _seed()
    _login(client, "admin@x.com")
    r = client.get(f"/api/admin/qr/{a}")
    assert r.status_code == 200
    assert r.get_json()["qr_code_path"].startswith("/static/")


def test_branch_code_path_traversal_rejected(client):
    with client.application.app_context():
        _seed()
    _login(client, "admin@x.com")
    r = client.post("/api/admin/branches", json={"name": "Evil", "code": "../../x"})
    assert r.status_code == 400


def test_manager_cannot_transfer_other_branch_token(client):
    with client.application.app_context():
        a, b, token_id = _seed()
        from app.models import Counter
        c = Counter(branch_id=b, label="B1")
        db.session.add(c)
        db.session.commit()
        counter_id = c.id
    _login(client, "mgr_a@x.com")
    r = client.post(f"/api/manager/token/{token_id}/transfer", json={"counter_id": counter_id})
    assert r.status_code == 403


def test_cross_origin_post_is_blocked(client):
    r = client.post("/api/auth/login", json={"email": "a", "password": "b"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_login_is_throttled_per_ip_without_locking_the_account(client):
    from app.api.auth import reset_login_throttle
    reset_login_throttle()
    client.application.config["LOGIN_IP_MAX_FAILURES"] = 5
    with client.application.app_context():
        _seed()
    attacker = {"REMOTE_ADDR": "203.0.113.9"}
    codes = [client.post("/api/auth/login", environ_base=attacker,
                         json={"email": "opd_a@x.com", "password": "bad"}).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[-1] == 429
    # The victim, from their own computer, can still sign in: failures from
    # someone else must not lock their account.
    ok = client.post("/api/auth/login", environ_base={"REMOTE_ADDR": "198.51.100.7"},
                     json={"email": "opd_a@x.com", "password": "secret123"})
    assert ok.status_code == 200
    reset_login_throttle()


def test_weak_password_rejected_for_new_admin_user(client):
    with client.application.app_context():
        _seed()
    _login(client, "admin@x.com")
    r = client.post("/api/admin/users", json={
        "employee_id": "E9", "full_name": "N", "email": "n@x.com",
        "password": "short", "role": "admin"})
    assert r.status_code == 400


def test_admin_cannot_deactivate_self(client):
    with client.application.app_context():
        _seed()
        admin_id = User.query.filter_by(email="admin@x.com").first().id
    _login(client, "admin@x.com")
    assert client.delete(f"/api/admin/users/{admin_id}").status_code == 400


def test_token_page_masks_mobile_and_headers_present(client):
    with client.application.app_context():
        _, _, token_id = _seed()
    r = client.get(f"/register/token/{_ref(client.application, token_id)}")
    assert r.status_code == 200
    assert b"+94771" not in r.data
    assert r.headers["X-Content-Type-Options"] == "nosniff"
