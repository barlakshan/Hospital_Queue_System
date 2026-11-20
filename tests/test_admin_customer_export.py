from io import BytesIO

import pytest
from openpyxl import load_workbook

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Customer, User, UserRole


@pytest.fixture()
def app_client(monkeypatch):
    monkeypatch.setattr(TestingConfig, "SQLALCHEMY_DATABASE_URI", "sqlite://")
    app = create_app("testing")

    with app.app_context():
        db.create_all()
        admin = User(
            employee_id="ADMIN001",
            full_name="Admin User",
            email="admin@example.com",
            role=UserRole.ADMIN,
        )
        admin.set_password("secret123")
        manager = User(
            employee_id="MGR001",
            full_name="Manager User",
            email="manager@example.com",
            role=UserRole.BRANCH_MANAGER,
        )
        manager.set_password("secret123")
        db.session.add_all([
            admin,
            manager,
            Customer(full_name="=1+1", mobile="+94770000000"),
            Customer(full_name="Customer Two", mobile="0771234567"),
        ])
        db.session.commit()

    yield app, app.test_client()

    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_admin_can_download_all_customer_contacts_as_excel(app_client):
    app, client = app_client
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "secret123"},
    )
    assert login.status_code == 200

    with app.app_context():
        customer = Customer.query.filter_by(full_name="Customer Two").one()
        customer.review_comments = "Private review note"
        customer.condition_summary = "Private treatment note"
        db.session.commit()

    page = client.get("/admin/customers")
    assert page.status_code == 200
    assert b"Export Excel" in page.data
    assert b"Patient Summary" in page.data
    assert b"NIC/Passport" not in page.data

    response = client.get("/admin/customers/export")

    assert response.status_code == 200
    assert response.mimetype == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["Content-Disposition"].endswith(
        'filename=hospital_customers.xlsx'
    )
    workbook = load_workbook(BytesIO(response.data), read_only=True, data_only=False)
    rows = list(workbook.active.iter_rows(values_only=True))
    assert rows[0] == ("Customer ID", "Full Name", "Mobile", "Registered")
    assert len(rows) == 3
    assert {row[1:3] for row in rows[1:]} == {
        ("=1+1", "+94770000000"),
        ("Customer Two", "0771234567"),
    }
    assert all(
        workbook.active.cell(row=row, column=column).data_type == "s"
        for row in range(2, 4)
        for column in (2, 3)
    )
    assert all("summary" not in header.lower() for header in rows[0])
    assert "Private review note" not in str(rows)
    assert "Private treatment note" not in str(rows)


def test_admin_can_save_patient_summary_and_limits_are_enforced(app_client):
    app, client = app_client
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "secret123"},
    )
    assert login.status_code == 200

    with app.app_context():
        customer_id = Customer.query.filter_by(full_name="Customer Two").one().id

    response = client.post(
        f"/admin/customers/{customer_id}/summary",
        data={
            "review_comments": "Follow-up requested",
            "condition_summary": "Reports recurring headaches",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        customer = db.session.get(Customer, customer_id)
        assert customer.review_comments == "Follow-up requested"
        assert customer.condition_summary == "Reports recurring headaches"

    too_long = client.post(
        f"/admin/customers/{customer_id}/summary",
        data={"condition_summary": "x" * 501},
        follow_redirects=True,
    )
    assert b"500 characters or fewer" in too_long.data
    with app.app_context():
        customer = db.session.get(Customer, customer_id)
        assert customer.condition_summary == "Reports recurring headaches"


def test_customer_export_is_restricted_to_admins(app_client):
    app, client = app_client
    assert client.get("/admin/customers/export").status_code == 302

    login = client.post(
        "/api/auth/login",
        json={"email": "manager@example.com", "password": "secret123"},
    )
    assert login.status_code == 200
    assert client.get("/admin/customers/export").status_code == 403
    with app.app_context():
        customer_id = Customer.query.first().id
    assert client.post(
        f"/admin/customers/{customer_id}/summary",
        data={"review_comments": "Not authorized"},
    ).status_code == 403


def test_admin_analytics_renders_a_canvas_for_the_chart(app_client):
    _, client = app_client
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "secret123"},
    )
    assert login.status_code == 200

    response = client.get("/admin/analytics")

    assert response.status_code == 200
    assert b'<canvas id="daily-token-chart"' in response.data
