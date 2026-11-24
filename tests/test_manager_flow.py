import re

import pytest

from app import create_app
from app.extensions import db
from app.models import Branch, Customer, QueueToken, ServiceType, User, UserRole


@pytest.fixture()
def client():
    app = create_app("testing")
    app.config.update(TESTING=True)

    with app.app_context():
        db.drop_all()
        db.create_all()

    with app.test_client() as client:
        yield client

    with app.app_context():
        db.drop_all()


def test_manager_can_manage_officers_for_own_branch(client):
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        db.session.add(branch)
        db.session.commit()

        manager = User(
            employee_id="MGR001",
            full_name="Manager Silva",
            email="manager@example.com",
            role=UserRole.BRANCH_MANAGER,
            branch_id=branch.id,
        )
        manager.set_password("secret123")
        db.session.add(manager)
        db.session.commit()

    login_response = client.post(
        "/api/auth/login",
        data={"email": "manager@example.com", "password": "secret123"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    officers_page = client.get("/manager/officers")
    assert officers_page.status_code == 200
    assert b"Officer Management" in officers_page.data

    create_response = client.post(
        "/manager/officers",
        data={
            "employee_id": "OFF001",
            "full_name": "Nimal Perera",
            "email": "nimal@example.com",
            "password": "secret123",
            "role": "OPD",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 302

    with client.application.app_context():
        created = User.query.filter_by(email="nimal@example.com").first()
        assert created is not None
        assert created.branch_id == 1
        assert created.role == UserRole.OPD


def test_manager_can_open_branch_qr_page_for_own_branch(client):
    with client.application.app_context():
        branch = Branch(code="KUR", name="Kurunegala", is_active=True)
        db.session.add(branch)
        db.session.commit()

        manager = User(
            employee_id="MGR002",
            full_name="Manager Perera",
            email="manager2@example.com",
            role=UserRole.BRANCH_MANAGER,
            branch_id=branch.id,
        )
        manager.set_password("secret123")
        db.session.add(manager)
        db.session.commit()

    client.post(
        "/api/auth/login",
        data={"email": "manager2@example.com", "password": "secret123"},
        follow_redirects=False,
    )

    response = client.get("/manager/qr")
    assert response.status_code == 200
    assert b"Branch registration QR" in response.data
    # The image URL is versioned by the encoded link, so changing BASE_URL
    # yields a new URL that browsers and nginx cannot serve from cache.
    old_src = re.search(rb'id="branch-qr" src="([^"]+)"', response.data).group(1)
    assert b"/static/qrcodes/KUR.png?v=" in old_src

    client.application.config["BASE_URL"] = "http://3.91.82.123"
    new_src = re.search(rb'id="branch-qr" src="([^"]+)"',
                        client.get("/manager/qr").data).group(1)
    assert new_src != old_src


def test_staff_member_can_open_branch_qr_page_for_assigned_branch(client):
    with client.application.app_context():
        branch = Branch(code="GAL", name="Galle", is_active=True)
        db.session.add(branch)
        db.session.commit()

        staff = User(
            employee_id="OPD001",
            full_name="OPD Officer",
            email="opd@example.com",
            role=UserRole.OPD,
            branch_id=branch.id,
        )
        staff.set_password("secret123")
        db.session.add(staff)
        db.session.commit()

    client.post(
        "/api/auth/login",
        data={"email": "opd@example.com", "password": "secret123"},
        follow_redirects=False,
    )

    response = client.get("/manager/qr")
    assert response.status_code == 200
    assert b"Branch registration QR" in response.data


def test_manager_can_update_condition_summary_for_patients_in_own_branch(client):
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        other_branch = Branch(code="GAL", name="Galle", is_active=True)
        db.session.add_all([branch, other_branch])
        db.session.commit()

        manager = User(
            employee_id="MGR003",
            full_name="Manager Fernando",
            email="manager3@example.com",
            role=UserRole.BRANCH_MANAGER,
            branch_id=branch.id,
        )
        manager.set_password("secret123")
        service = ServiceType(
            code="G",
            name="General OPD",
            prefix="G",
            allowed_roles=["opd"],
        )
        own_patient = Customer(
            full_name="Own Branch Patient",
            mobile="0770000001",
            review_comments="Keep this note",
        )
        other_patient = Customer(
            full_name="Other Branch Patient",
            mobile="0770000002",
        )
        db.session.add_all([manager, service, own_patient, other_patient])
        db.session.commit()
        db.session.add_all([
            QueueToken(
                branch_id=branch.id,
                customer_id=own_patient.id,
                service_type_id=service.id,
                token_number="G001",
            ),
            QueueToken(
                branch_id=other_branch.id,
                customer_id=other_patient.id,
                service_type_id=service.id,
                token_number="G001",
            ),
        ])
        db.session.commit()
        own_patient_id = own_patient.id
        other_patient_id = other_patient.id

    client.post(
        "/api/auth/login",
        data={"email": "manager3@example.com", "password": "secret123"},
        follow_redirects=False,
    )

    patients_response = client.get("/manager/customers")
    assert patients_response.status_code == 200
    assert b"Patient Management" in patients_response.data
    assert b"Own Branch Patient" in patients_response.data
    assert b"Keep this note" not in patients_response.data

    update_response = client.post(
        f"/manager/customers/{own_patient_id}/summary",
        data={"condition_summary": "Reports recurring headaches."},
        follow_redirects=False,
    )
    assert update_response.status_code == 302
    assert update_response.headers["Location"].endswith("/manager/customers")

    with client.application.app_context():
        own_patient = db.session.get(Customer, own_patient_id)
        assert own_patient.condition_summary == "Reports recurring headaches."
        assert own_patient.review_comments == "Keep this note"

    forbidden_patient_response = client.post(
        f"/manager/customers/{other_patient_id}/summary",
        data={"condition_summary": "Out-of-branch update."},
    )
    assert forbidden_patient_response.status_code == 404

    with client.application.app_context():
        other_patient = db.session.get(Customer, other_patient_id)
        assert other_patient.condition_summary is None
