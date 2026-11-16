import pytest

from app import create_app
from app.extensions import db
from app.models import (Branch, Counter, QueueToken, SatisfactionRating, ServiceType,
                        TokenStatus, User, UserRole)


@pytest.fixture()
def client():
    app = create_app("testing")
    app.config.update(TESTING=True, SMS_PROVIDER="mock")

    with app.app_context():
        db.drop_all()
        db.create_all()
        for service in [
            ServiceType(code="G", name="General OPD", prefix="G", avg_service_min=10,
                        allowed_roles=["lobby_officer"]),
            ServiceType(code="S", name="Specialist Consultation", prefix="S", avg_service_min=15,
                        allowed_roles=["lobby_officer", "loan_officer", "specialist_doctor"]),
            ServiceType(code="L", name="Laboratory", prefix="L", avg_service_min=8,
                        allowed_roles=["loan_officer"]),
            ServiceType(code="R", name="Radiology", prefix="R", avg_service_min=12,
                        allowed_roles=["loan_officer"]),
            ServiceType(code="P", name="Pharmacy", prefix="P", avg_service_min=6,
                        allowed_roles=["lobby_officer", "loan_officer"]),
            ServiceType(code="B", name="Billing / Cashier", prefix="B", avg_service_min=5,
                        allowed_roles=["lobby_officer", "loan_officer"]),
        ]:
            db.session.add(service)
        db.session.commit()

    with app.test_client() as client:
        yield client

    with app.app_context():
        db.drop_all()


def test_public_display_route_renders_live_screen(client):
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        db.session.add(branch)
        db.session.commit()

    response = client.get("/display")
    assert response.status_code == 200
    assert b"Queue Display" in response.data
    assert b"Colombo" in response.data


def test_customer_registration_and_queue_lifecycle(client):
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        db.session.add(branch)
        db.session.commit()
        officer = User(
            employee_id="OFF001",
            full_name="Officer One",
            email="officer@example.com",
            role=UserRole.LOBBY_OFFICER,
            branch_id=branch.id,
        )
        officer.set_password("secret123")
        db.session.add(officer)
        db.session.commit()
        counter = Counter(branch_id=branch.id, label="OPD 1", user_id=officer.id)
        db.session.add(counter)
        db.session.commit()
        service = ServiceType.query.filter_by(code="G").first()
        branch_id = branch.id
        service_id = service.id
        counter_id = counter.id

    login_response = client.post(
        "/api/auth/login",
        json={"email": "officer@example.com", "password": "secret123"},
    )
    assert login_response.status_code == 200

    register_response = client.post(
        "/api/queue/register",
        json={
            "branch_id": branch_id,
            "full_name": "Nimal Perera",
            "mobile": "0771234567",
            "service_type_id": service_id,
        },
    )
    assert register_response.status_code == 201
    payload = register_response.get_json()
    assert payload["token_number"].startswith("G")
    assert payload["position"] >= 0

    with client.application.app_context():
        token = QueueToken.query.get(payload["token_id"])
        assert token is not None
        assert token.status == TokenStatus.WAITING
        assert token.estimated_wait_min >= 0

    status_response = client.get(f"/api/queue/token/{payload['token_id']}/status")
    assert status_response.status_code == 200
    assert status_response.get_json()["status"] == "waiting"

    call_response = client.post("/api/queue/call-next", json={"counter_id": counter_id})
    assert call_response.status_code == 200

    start_response = client.post(f"/api/queue/token/{payload['token_id']}/start")
    assert start_response.status_code == 200

    complete_response = client.post(f"/api/queue/token/{payload['token_id']}/complete")
    assert complete_response.status_code == 200

    rating_response = client.post(
        f"/api/queue/token/{payload['token_id']}/rate",
        json={"rating": 5, "comment": "Great service"},
    )
    assert rating_response.status_code == 200

    with client.application.app_context():
        token = QueueToken.query.get(payload["token_id"])
        assert token.status == TokenStatus.COMPLETED
        assert SatisfactionRating.query.filter_by(token_id=token.id).count() == 1


def test_registration_landing_and_qr_use_a_working_request_url(client):
    client.application.config["BASE_URL"] = ""
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        db.session.add(branch)
        db.session.commit()
        branch_id = branch.id
        service_id = ServiceType.query.filter_by(code="G").first().id

    landing = client.get("/register/")
    assert landing.status_code == 302
    assert landing.headers["Location"].endswith("/register/COL")

    form = client.get(landing.headers["Location"])
    assert form.status_code == 200
    assert b"Patient Queue Registration" in form.data

    with client.application.test_request_context(
        "/", base_url="http://127.0.0.1:5000"
    ):
        with client.application.app_context():
            branch = Branch.query.filter_by(code="COL").one()
            assert branch.qr_payload() == "http://127.0.0.1:5000/register/COL"
            client.application.config["BASE_URL"] = "https://hospital.example/"
            assert branch.qr_payload() == "https://hospital.example/register/COL"

    with client.application.app_context():
        db.session.add(Branch(code="GAL", name="Galle", is_active=True))
        db.session.commit()
    picker = client.get("/register/")
    assert picker.status_code == 200
    assert b"/register/COL" in picker.data
    assert b"/register/GAL" in picker.data

    registration = client.post(
        "/api/queue/register",
        json={
            "branch_id": branch_id,
            "full_name": "Nimal Perera",
            "mobile": "0771234567",
            "service_type_id": service_id,
        },
    )
    assert registration.status_code == 201
    assert registration.get_json()["token_id"]


def test_admin_can_manage_branches_and_users(client):
    with client.application.app_context():
        admin = User(
            employee_id="ADM001",
            full_name="Admin One",
            email="admin@example.com",
            role=UserRole.ADMIN,
        )
        admin.set_password("secret123")
        db.session.add(admin)
        db.session.commit()

    login_response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "secret123"},
    )
    assert login_response.status_code == 200

    dashboard_response = client.get("/admin/dashboard")
    assert dashboard_response.status_code == 200

    branches_response = client.get("/api/admin/branches")
    assert branches_response.status_code == 200

    create_branch_response = client.post(
        "/api/admin/branches",
        json={"name": "Galle", "code": "GAL"},
    )
    assert create_branch_response.status_code == 201

    users_response = client.get("/api/admin/users")
    assert users_response.status_code == 200


def test_admin_user_list_displays_all_roles_with_visible_labels(client):
    with client.application.app_context():
        admin = User(
            employee_id="ADM002",
            full_name="Admin Two",
            email="admin2@example.com",
            role=UserRole.ADMIN,
        )
        admin.set_password("secret123")
        db.session.add(admin)
        for index, role in enumerate(UserRole):
            user = User(
                employee_id=f"USR{index:03}",
                full_name=f"User {role.name}",
                email=f"{role.name.lower()}@example.com",
                role=role,
            )
            user.set_password("secret123")
            db.session.add(user)
        db.session.commit()

    login_response = client.post(
        "/api/auth/login",
        json={"email": "admin2@example.com", "password": "secret123"},
    )
    assert login_response.status_code == 200

    response = client.get("/admin/users")
    assert response.status_code == 200
    for role_label, badge_class in [
        ("System Administrator", "red"),
        ("Department / Operations Manager", "yellow"),
        ("Reception / OPD Staff", "green"),
        ("Department Staff", "dark"),
        ("Specialist Doctor", "dark"),
    ]:
        assert role_label.encode() in response.data
        assert f'class="pb-badge {badge_class}"'.encode() in response.data


def test_admin_can_add_named_specialist_doctor_for_consultation_queue(client):
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        admin = User(
            employee_id="ADM003",
            full_name="Admin Three",
            email="admin3@example.com",
            role=UserRole.ADMIN,
        )
        admin.set_password("secret123")
        db.session.add_all([branch, admin])
        db.session.commit()
        branch_id = branch.id
        specialist_service_id = ServiceType.query.filter_by(code="S").one().id

    client.post(
        "/api/auth/login",
        json={"email": "admin3@example.com", "password": "secret123"},
    )
    form_response = client.get("/admin/users/new")
    assert b"Specialist Doctor" in form_response.data

    create_response = client.post(
        "/admin/users/new",
        data={
            "employee_id": "DOC001",
            "full_name": "Dr. Asha Silva",
            "email": "asha@example.com",
            "password": "secret123",
            "role": "SPECIALIST_DOCTOR",
            "branch_id": str(branch_id),
        },
    )
    assert create_response.status_code == 302

    with client.application.app_context():
        doctor = User.query.filter_by(email="asha@example.com").one()
        assert doctor.full_name == "Dr. Asha Silva"
        assert doctor.role == UserRole.SPECIALIST_DOCTOR
        assert doctor.allowed_service_codes == ["S"]
        counter = Counter(branch_id=branch_id, user_id=doctor.id, label="Consultation 1")
        db.session.add(counter)
        db.session.commit()
        counter_id = counter.id

    registration = client.post(
        "/api/queue/register",
        json={
            "branch_id": branch_id,
            "full_name": "Patient One",
            "mobile": "0771234567",
            "service_type_id": specialist_service_id,
        },
    )
    assert registration.status_code == 201

    doctor_login = client.post(
        "/api/auth/login",
        json={"email": "asha@example.com", "password": "secret123"},
    )
    assert doctor_login.status_code == 200
    assert client.get("/officer/dashboard").status_code == 200
    call_response = client.post("/api/queue/call-next", json={"counter_id": counter_id})
    assert call_response.status_code == 200
    assert call_response.get_json()["service"] == "Specialist Consultation"


def test_manager_can_manage_officers_and_open_qr(client):
    with client.application.app_context():
        branch = Branch(code="KUR", name="Kurunegala", is_active=True)
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
        json={"email": "manager@example.com", "password": "secret123"},
    )
    assert login_response.status_code == 200

    officers_page = client.get("/manager/officers")
    assert officers_page.status_code == 200

    create_response = client.post(
        "/manager/officers",
        data={
            "employee_id": "OFF005",
            "full_name": "Nimal Perera",
            "email": "nimal@example.com",
            "password": "secret123",
            "role": "LOBBY_OFFICER",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 302

    with client.application.app_context():
        created = User.query.filter_by(email="nimal@example.com").first()
        assert created is not None
        assert created.branch_id is not None
        assert created.role == UserRole.LOBBY_OFFICER

    qr_response = client.get("/manager/qr")
    assert qr_response.status_code == 200


def test_officer_can_view_dashboard_and_queue(client):
    with client.application.app_context():
        branch = Branch(code="MAT", name="Matara", is_active=True)
        db.session.add(branch)
        db.session.commit()
        officer = User(
            employee_id="OFF010",
            full_name="Officer Matara",
            email="officer2@example.com",
            role=UserRole.LOBBY_OFFICER,
            branch_id=branch.id,
        )
        officer.set_password("secret123")
        db.session.add(officer)
        db.session.commit()

    login_response = client.post(
        "/api/auth/login",
        json={"email": "officer2@example.com", "password": "secret123"},
    )
    assert login_response.status_code == 200

    dashboard_response = client.get("/officer/dashboard")
    assert dashboard_response.status_code == 200

    queue_response = client.get("/officer/queue")
    assert queue_response.status_code == 200

    history_response = client.get("/officer/history")
    assert history_response.status_code == 200


def test_hospital_priority_categories_keep_priority_ahead_of_regular_queue(client):
    with client.application.app_context():
        branch = Branch(code="HOS", name="Main Hospital", is_active=True)
        db.session.add(branch)
        db.session.commit()
        officer = User(
            employee_id="NUR001",
            full_name="Nurse One",
            email="nurse@example.com",
            role=UserRole.LOBBY_OFFICER,
            branch_id=branch.id,
        )
        officer.set_password("secret123")
        db.session.add(officer)
        db.session.commit()
        branch_id = branch.id
        service_id = ServiceType.query.filter_by(code="G").first().id

    mobile_counter = [0]

    def register_patient(name, priority="NONE"):
        mobile_counter[0] += 1
        return client.post("/api/queue/register", json={
            "branch_id": branch_id,
            "full_name": name,
            "mobile": f"07712345{mobile_counter[0]:02d}",
            "service_type_id": service_id,
            "priority_reason": priority,
        })

    first_regular = register_patient("Patient One")
    emergency = register_patient("Patient Two", "EMERGENCY")
    second_regular = register_patient("Patient Three")
    urgent = register_patient("Patient Four", "URGENT")
    assert [first_regular.status_code, emergency.status_code,
            second_regular.status_code, urgent.status_code] == [201, 201, 201, 201]

    assert emergency.get_json()["position"] == 0
    assert second_regular.get_json()["position"] == 2
    assert urgent.get_json()["position"] == 1
    status = client.get(
        f"/api/queue/token/{second_regular.get_json()['token_id']}/status"
    ).get_json()
    assert status["estimated_wait"] == 30
    assert register_patient("Invalid Priority", "CRITICAL").status_code == 400

    assert client.post(
        "/api/auth/login",
        json={"email": "nurse@example.com", "password": "secret123"},
    ).status_code == 200
    call_response = client.post("/api/queue/call-next", json={})
    assert call_response.status_code == 200
    assert call_response.get_json()["token_number"] == emergency.get_json()["token_number"]


def test_service_configuration_controls_staff_queue_access_and_display_privacy(client):
    with client.application.app_context():
        branch = Branch(code="PRI", name="Private Hospital", is_active=True)
        admin = User(
            employee_id="ADM100",
            full_name="Hospital Admin",
            email="hospital-admin@example.com",
            role=UserRole.ADMIN,
        )
        admin.set_password("secret123")
        db.session.add_all([branch, admin])
        db.session.commit()
        service = ServiceType.query.filter_by(code="G").first()
        service.allowed_roles = ["lobby_officer"]
        service_id = service.id
        branch_id = branch.id
        db.session.commit()

        receptionist = User(
            employee_id="REC100",
            full_name="Reception Staff",
            email="reception@example.com",
            role=UserRole.LOBBY_OFFICER,
            branch_id=branch_id,
        )
        receptionist.set_password("secret123")
        db.session.add(receptionist)
        db.session.commit()
        assert receptionist.allowed_service_codes == ["G", "S", "P", "B"]

    assert client.post(
        "/api/auth/login",
        json={"email": "hospital-admin@example.com", "password": "secret123"},
    ).status_code == 200
    services_page = client.get("/admin/services")
    assert services_page.status_code == 200
    assert b"Departments &amp; Services" in services_page.data
    with client.application.app_context():
        service_rows = ServiceType.query.filter_by(is_active=True).all()
        settings = {}
        for service in service_rows:
            settings[f"name_{service.id}"] = service.name
            settings[f"prefix_{service.id}"] = service.prefix
            settings[f"avg_{service.id}"] = str(service.avg_service_min)
            settings[f"active_{service.id}"] = "1"
            settings[f"roles_{service.id}"] = service.allowed_roles or []
        settings[f"roles_{service_id}"] = ["loan_officer"]
        settings.update({
            "new_code": "X",
            "new_name": "Vaccination",
            "new_prefix": "V",
            "new_avg": "20",
            "new_roles": ["loan_officer"],
        })
    assert client.post("/admin/services", data=settings).status_code == 302
    with client.application.app_context():
        assert ServiceType.query.get(service_id).allowed_roles == ["loan_officer"]
        vaccination = ServiceType.query.filter_by(code="X").first()
        assert vaccination is not None
        assert vaccination.avg_service_min == 20
        assert vaccination.allowed_roles == ["loan_officer"]

    patient_response = client.post("/api/queue/register", json={
        "branch_id": branch_id,
        "full_name": "Private Patient",
        "mobile": "0771234599",
        "service_type_id": service_id,
        "priority_reason": "DISABLED",
    })
    assert patient_response.status_code == 201
    token_id = patient_response.get_json()["token_id"]

    with client.application.app_context():
        from app.models import QueueToken, TokenStatus
        token = QueueToken.query.get(token_id)
        token.status = TokenStatus.CALLED
        db.session.commit()

    display_response = client.get("/api/queue/display/PRI")
    assert display_response.status_code == 200
    displayed_token = display_response.get_json()["serving"][0]
    assert "customer_name" not in displayed_token
    assert "priority_reason" not in displayed_token


def test_manager_cannot_access_admin_routes(client):
    with client.application.app_context():
        branch = Branch(code="BAD", name="Badulla", is_active=True)
        db.session.add(branch)
        db.session.commit()
        manager = User(
            employee_id="MGR010",
            full_name="Manager Badulla",
            email="manager2@example.com",
            role=UserRole.BRANCH_MANAGER,
            branch_id=branch.id,
        )
        manager.set_password("secret123")
        db.session.add(manager)
        db.session.commit()

    login_response = client.post(
        "/api/auth/login",
        json={"email": "manager2@example.com", "password": "secret123"},
    )
    assert login_response.status_code == 200

    admin_dashboard_response = client.get("/admin/dashboard")
    assert admin_dashboard_response.status_code == 403

    admin_branches_response = client.get("/api/admin/branches")
    assert admin_branches_response.status_code == 403
