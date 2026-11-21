import pytest

from app import create_app
from app.extensions import db
from app.models import (AppointmentRequest, Branch, Counter, QueueToken,
                        SatisfactionRating, ServiceType, TokenStatus, User,
                        UserRole)


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

    waiting_before_refresh = client.get("/api/queue/waiting")
    assert waiting_before_refresh.status_code == 200
    assert [token["id"] for token in waiting_before_refresh.get_json()] == [
        payload["token_id"]
    ]
    assert client.get("/officer/dashboard").status_code == 200
    waiting_after_refresh = client.get("/api/queue/waiting")
    assert waiting_after_refresh.status_code == 200
    assert [token["id"] for token in waiting_after_refresh.get_json()] == [
        payload["token_id"]
    ]

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
    assert call_response.get_json()["token_id"] == payload["token_id"]

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


def test_officer_dashboard_serves_without_an_assigned_counter(client):
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        db.session.add(branch)
        db.session.commit()
        officer = User(
            employee_id="OFF002",
            full_name="Officer Two",
            email="officer2@example.com",
            role=UserRole.LOBBY_OFFICER,
            branch_id=branch.id,
        )
        officer.set_password("secret123")
        db.session.add(officer)
        db.session.commit()
        branch_id = branch.id
        service_id = ServiceType.query.filter_by(code="G").first().id

    assert client.post(
        "/api/auth/login",
        json={"email": "officer2@example.com", "password": "secret123"},
    ).status_code == 200

    dashboard = client.get("/officer/dashboard")
    assert dashboard.status_code == 200
    assert b"OfficerConsole.init(null" in dashboard.data
    assert dashboard.data.count(b"officer-console.js") == 1

    registration = client.post(
        "/api/queue/register",
        json={
            "branch_id": branch_id,
            "full_name": "Patient Two",
            "mobile": "0771234568",
            "service_type_id": service_id,
        },
    )
    token_id = registration.get_json()["token_id"]
    call = client.post("/api/queue/call-next", json={"counter_id": None})
    assert call.status_code == 200
    assert call.get_json()["token_id"] == token_id

    complete = client.post(f"/api/queue/token/{token_id}/complete")
    assert complete.status_code == 200


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
        ("Admin", "red"),
        ("OPD", "green"),
        ("Pharmacy", "yellow"),
        ("Dentist", "dark"),
        ("Cardiologists", "dark"),
        ("Dermatologists", "dark"),
        ("Department / Operations Manager", "yellow"),
        ("Reception / OPD Staff", "green"),
        ("Department Staff", "dark"),
        ("Specialist Doctor", "dark"),
    ]:
        assert role_label.encode() in response.data
        assert f'class="pb-badge {badge_class}"'.encode() in response.data


def test_admin_can_add_named_dentist_for_consultation_queue(client):
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

    registration_page = client.get("/register/COL")
    assert registration_page.status_code == 200
    assert b'<select id="service-type" name="service_type_id"' in registration_page.data
    assert (
        f'<option value="{specialist_service_id}">Specialist Consultation</option>'.encode()
        in registration_page.data
    )

    client.post(
        "/api/auth/login",
        json={"email": "admin3@example.com", "password": "secret123"},
    )
    form_response = client.get("/admin/users/new")
    assert b"Dentist" in form_response.data
    assert b"Specialist Doctor" not in form_response.data

    create_response = client.post(
        "/admin/users/new",
        data={
            "employee_id": "DOC001",
            "full_name": "Dr. Asha Silva",
            "email": "asha@example.com",
            "password": "secret123",
            "role": "DENTIST",
            "branch_id": str(branch_id),
            "service_ids": [str(specialist_service_id)],
        },
    )
    assert create_response.status_code == 302

    with client.application.app_context():
        doctor = User.query.filter_by(email="asha@example.com").one()
        assert doctor.full_name == "Dr. Asha Silva"
        assert doctor.role == UserRole.DENTIST
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
    waiting_response = client.get("/api/queue/waiting")
    assert waiting_response.status_code == 200
    assert waiting_response.get_json()[0]["customer_name"] == "Patient One"
    assert waiting_response.get_json()[0]["service"] == "Specialist Consultation"
    call_response = client.post("/api/queue/call-next", json={"counter_id": counter_id})
    assert call_response.status_code == 200
    assert call_response.get_json()["service"] == "Specialist Consultation"


def test_manager_can_manage_opd_staff_and_open_qr(client):
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
            "role": "OPD",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 302

    with client.application.app_context():
        created = User.query.filter_by(email="nimal@example.com").first()
        assert created is not None
        assert created.branch_id is not None
        assert created.role == UserRole.OPD

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


def test_appointment_requests_are_visible_to_admin_and_matching_doctor_role(client):
    with client.application.app_context():
        branch = Branch(code="COL", name="Colombo", is_active=True)
        db.session.add(branch)
        db.session.flush()
        accounts = []
        for employee_id, email, role in (
            ("ADM100", "appointments-admin@example.com", UserRole.ADMIN),
            ("DOC100", "dentist@example.com", UserRole.DENTIST),
            ("DOC101", "dermatologist@example.com", UserRole.DERMATOLOGISTS),
        ):
            user = User(
                employee_id=employee_id,
                full_name=email.split("@")[0],
                email=email,
                role=role,
                branch_id=branch.id if role != UserRole.ADMIN else None,
            )
            user.set_password("secret123")
            accounts.append(user)
        db.session.add_all(accounts)
        db.session.commit()
        branch_id = branch.id
        dental = ServiceType(
            code="DENT", name="Dental", prefix="D",
            allowed_roles=[UserRole.DENTIST.value],
        )
        dermatology = ServiceType(
            code="DERM", name="Dermatology", prefix="M",
            allowed_roles=[UserRole.DERMATOLOGISTS.value],
        )
        db.session.add_all([dental, dermatology])
        db.session.commit()
        dental_service_id = dental.id
        dermatology_service_id = dermatology.id
        User.query.filter_by(email="dentist@example.com").one().assigned_services = [dental]
        User.query.filter_by(email="dermatologist@example.com").one().assigned_services = [dermatology]
        db.session.add(AppointmentRequest(
            branch_id=branch_id,
            full_name="Misrouted service request",
            mobile="0771234569",
            service_type_id=dental_service_id,
            requested_role=UserRole.DERMATOLOGISTS.value,
        ))
        db.session.commit()

    invalid_response = client.post(
        "/api/queue/appointments",
        json={
            "branch_id": branch_id,
            "full_name": "Patient Appointment",
            "mobile": "0771234567",
            "requested_role": "pharmacy",
        },
    )
    assert invalid_response.status_code == 400

    response = client.post(
        "/api/queue/appointments",
        json={
            "branch_id": branch_id,
            "full_name": "Patient Appointment",
            "mobile": "0771234567",
            "service_type_id": dental_service_id,
        },
    )
    assert response.status_code == 201
    assert response.get_json()["status"] == "pending"
    dermatology_response = client.post(
        "/api/queue/appointments",
        json={
            "branch_id": branch_id,
            "full_name": "Patient Dermatology",
            "mobile": "0771234568",
            "service_type_id": dermatology_service_id,
        },
    )
    assert dermatology_response.status_code == 201

    client.post(
        "/api/auth/login",
        json={"email": "appointments-admin@example.com", "password": "secret123"},
    )
    admin_dashboard = client.get("/admin/dashboard")
    assert admin_dashboard.status_code == 200
    assert b"Patient Appointment" in admin_dashboard.data

    client.post(
        "/api/auth/login",
        json={"email": "dentist@example.com", "password": "secret123"},
    )
    dentist_dashboard = client.get("/officer/dashboard")
    assert dentist_dashboard.status_code == 200
    assert b"Patient Appointment" in dentist_dashboard.data
    assert b"Patient Dermatology" not in dentist_dashboard.data

    client.post(
        "/api/auth/login",
        json={"email": "dermatologist@example.com", "password": "secret123"},
    )
    dermatologist_dashboard = client.get("/officer/dashboard")
    assert dermatologist_dashboard.status_code == 200
    assert b"Patient Appointment" not in dermatologist_dashboard.data
    assert b"Patient Dermatology" in dermatologist_dashboard.data
    assert b"Misrouted service request" not in dermatologist_dashboard.data


def test_specialist_dashboard_only_loads_its_role_services(client):
    with client.application.app_context():
        branch = Branch(code="SPC", name="Specialty Clinic", is_active=True)
        db.session.add(branch)
        db.session.flush()
        cardiology = ServiceType(
            code="CARD", name="Cardiologists", prefix="C",
            allowed_roles=[UserRole.CARDIOLOGISTS.value],
        )
        dermatology = ServiceType(
            code="DERM", name="Dermatologists", prefix="M",
            allowed_roles=[UserRole.DERMATOLOGISTS.value],
        )
        admin = User(
            employee_id="ADM300",
            full_name="Specialty Admin",
            email="specialty-admin@example.com",
            role=UserRole.ADMIN,
        )
        dermatologist = User(
            employee_id="DOC300",
            full_name="Dermatology Officer",
            email="dermatology-officer@example.com",
            role=UserRole.DERMATOLOGISTS,
            branch_id=branch.id,
        )
        cardiologist = User(
            employee_id="DOC301",
            full_name="Cardiology Officer",
            email="cardiology-officer@example.com",
            role=UserRole.CARDIOLOGISTS,
            branch_id=branch.id,
        )
        admin.set_password("secret123")
        dermatologist.set_password("secret123")
        cardiologist.set_password("secret123")
        db.session.add_all([cardiology, dermatology, admin, dermatologist, cardiologist])
        db.session.commit()
        dermatologist.assigned_services = [cardiology, dermatology]
        cardiologist.assigned_services = [cardiology]
        branch_id = branch.id
        cardiology_id = cardiology.id
        dermatology_id = dermatology.id
        db.session.commit()

    cardiology_token = client.post(
        "/api/queue/register",
        json={
            "branch_id": branch_id,
            "full_name": "Cardiology Patient",
            "mobile": "0771234570",
            "service_type_id": cardiology_id,
        },
    ).get_json()["token_id"]
    dermatology_token = client.post(
        "/api/queue/register",
        json={
            "branch_id": branch_id,
            "full_name": "Dermatology Patient",
            "mobile": "0771234571",
            "service_type_id": dermatology_id,
        },
    ).get_json()["token_id"]
    specialty_appointment = client.post(
        "/api/queue/appointments",
        json={
            "branch_id": branch_id,
            "full_name": "Dermatology Appointment",
            "mobile": "0771234572",
            "requested_role": UserRole.DERMATOLOGISTS.value,
        },
    )
    assert specialty_appointment.status_code == 201
    assert specialty_appointment.get_json()["service_type_id"] == dermatology_id

    invalid_assignment = client.post(
        "/api/auth/login",
        json={"email": "specialty-admin@example.com", "password": "secret123"},
    )
    assert invalid_assignment.status_code == 200
    rejected_user = client.post(
        "/api/admin/users",
        json={
            "employee_id": "DOC302",
            "full_name": "Wrong Specialty",
            "email": "wrong-specialty@example.com",
            "password": "secret123",
            "role": "DERMATOLOGISTS",
            "branch_id": branch_id,
            "service_ids": [cardiology_id],
        },
    )
    assert rejected_user.status_code == 400

    assert client.post(
        "/api/auth/login",
        json={"email": "dermatology-officer@example.com", "password": "secret123"},
    ).status_code == 200
    dermatology_queue = client.get("/api/queue/waiting").get_json()
    assert [token["id"] for token in dermatology_queue] == [dermatology_token]
    assert b"Cardiology Patient" not in client.get("/officer/queue").data
    assert b"Dermatology Patient" in client.get("/officer/queue").data
    call = client.post("/api/queue/call-next", json={"counter_id": None})
    assert call.status_code == 200
    assert call.get_json()["token_id"] == dermatology_token

    assert client.post(
        "/api/auth/login",
        json={"email": "cardiology-officer@example.com", "password": "secret123"},
    ).status_code == 200
    cardiology_queue = client.get("/api/queue/waiting").get_json()
    assert [token["id"] for token in cardiology_queue] == [cardiology_token]


def test_clean_data_requires_confirmation_and_preserves_admin_accounts(client):
    with client.application.app_context():
        branch = Branch(code="KUR", name="Kurunegala", is_active=True)
        db.session.add(branch)
        db.session.flush()
        admin = User(
            employee_id="ADM200",
            full_name="Retained Admin",
            email="retained-admin@example.com",
            role=UserRole.ADMIN,
            branch_id=branch.id,
        )
        staff = User(
            employee_id="DOC200",
            full_name="Removed Doctor",
            email="removed-doctor@example.com",
            role=UserRole.DENTIST,
            branch_id=branch.id,
        )
        admin.set_password("secret123")
        staff.set_password("secret123")
        db.session.add_all([admin, staff])
        db.session.add(ServiceType(
            code="TMP", name="Temporary", prefix="T", avg_service_min=10,
            allowed_roles=["dentist"],
        ))
        db.session.commit()

    runner = client.application.test_cli_runner()
    refused = runner.invoke(args=["clean-data"])
    assert refused.exit_code != 0

    cleaned = runner.invoke(args=["clean-data", "--confirm"])
    assert cleaned.exit_code == 0
    assert "locations and services" in cleaned.output

    with client.application.app_context():
        assert User.query.filter_by(role=UserRole.ADMIN).count() == 1
        assert User.query.filter_by(role=UserRole.DENTIST).count() == 0
        assert Branch.query.count() == 0
        assert ServiceType.query.count() == 0

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


def test_admin_can_create_update_deactivate_reactivate_and_delete_services(client):
    with client.application.app_context():
        admin = User(
            employee_id="ADM210",
            full_name="Hospital Admin",
            email="services-admin@example.com",
            role=UserRole.ADMIN,
        )
        admin.set_password("secret123")
        db.session.add(admin)
        db.session.commit()

    assert client.post(
        "/api/auth/login",
        json={"email": "services-admin@example.com", "password": "secret123"},
    ).status_code == 200
    assert client.post(
        "/admin/services", data={"new_name": "Vaccination"}
    ).status_code == 302

    with client.application.app_context():
        service = ServiceType.query.filter_by(name="Vaccination").one()
        service_id = service.id

    assert client.post(
        f"/admin/services/{service_id}/edit",
        data={
            "name": "Immunization",
            "prefix": "IM",
            "avg_service_min": "20",
            "allowed_roles": ["dentist"],
        },
    ).status_code == 302
    with client.application.app_context():
        service = ServiceType.query.get(service_id)
        assert service.name == "Immunization"
        assert service.prefix == "IM"
        assert service.avg_service_min == 20
        assert service.allowed_roles == ["dentist"]

    assert client.post(
        f"/admin/services/{service_id}/edit",
        data={
            "name": "Changed Name",
            "prefix": "S",
            "avg_service_min": "25",
            "allowed_roles": ["opd"],
        },
    ).status_code == 302
    with client.application.app_context():
        service = ServiceType.query.get(service_id)
        assert service.name == "Immunization"
        assert service.prefix == "IM"
        assert service.avg_service_min == 20
        assert service.allowed_roles == ["dentist"]

    assert client.post(
        f"/admin/services/{service_id}/status", data={"action": "deactivate"}
    ).status_code == 302
    with client.application.app_context():
        assert ServiceType.query.get(service_id).is_active is False
    assert b"Immunization" in client.get("/admin/services").data

    assert client.post(
        f"/admin/services/{service_id}/status", data={"action": "activate"}
    ).status_code == 302
    with client.application.app_context():
        assert ServiceType.query.get(service_id).is_active is True

    assert client.post(f"/admin/services/{service_id}/delete").status_code == 302
    with client.application.app_context():
        assert ServiceType.query.get(service_id) is None


def test_admin_cannot_delete_a_service_assigned_to_a_user(client):
    with client.application.app_context():
        service = ServiceType(code="VAC", name="Vaccination", prefix="V")
        admin = User(
            employee_id="ADM211",
            full_name="Hospital Admin",
            email="assigned-services-admin@example.com",
            role=UserRole.ADMIN,
            assigned_services=[service],
        )
        admin.set_password("secret123")
        db.session.add_all([service, admin])
        db.session.commit()
        service_id = service.id

    assert client.post(
        "/api/auth/login",
        json={"email": "assigned-services-admin@example.com", "password": "secret123"},
    ).status_code == 200
    assert client.post(f"/admin/services/{service_id}/delete").status_code == 302
    with client.application.app_context():
        assert ServiceType.query.get(service_id) is not None


def test_added_services_are_assignable_to_users_and_display_privacy_is_preserved(client):
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
        receptionist_id = receptionist.id

    assert client.post(
        "/api/auth/login",
        json={"email": "hospital-admin@example.com", "password": "secret123"},
    ).status_code == 200
    services_page = client.get("/admin/services")
    assert services_page.status_code == 200
    assert b"Departments &amp; Services" in services_page.data
    assert b"Staff roles allowed to call this queue" in services_page.data
    assert b"Average service time (minutes)" in services_page.data
    assert client.post(
        "/admin/services", data={"new_name": "Vaccination"}
    ).status_code == 302
    with client.application.app_context():
        vaccination = ServiceType.query.filter_by(name="Vaccination").first()
        assert vaccination is not None
        assert vaccination.avg_service_min == 10
        assert vaccination.code
        assert vaccination.prefix
        assert vaccination.allowed_roles == []
        vaccination_id = vaccination.id
        vaccination_code = vaccination.code

    ticket_form = client.get("/register/PRI")
    assert ticket_form.status_code == 200
    assert f'<option value="{vaccination_id}">Vaccination</option>'.encode() in ticket_form.data
    user_form = client.get("/admin/users/new")
    assert user_form.status_code == 200
    assert b'name="service_ids"' in user_form.data
    assert b"Vaccination" in user_form.data

    assignment_response = client.put(
        f"/api/admin/users/{receptionist_id}",
        json={"service_ids": [vaccination_id]},
    )
    assert assignment_response.status_code == 200
    with client.application.app_context():
        receptionist = User.query.get(receptionist_id)
        assert receptionist.allowed_service_codes == [vaccination_code]

    patient_response = client.post("/api/queue/register", json={
        "branch_id": branch_id,
        "full_name": "Private Patient",
        "mobile": "0771234599",
        "service_type_id": service_id,
        "priority_reason": "DISABLED",
    })
    assert patient_response.status_code == 201
    token_id = patient_response.get_json()["token_id"]
    vaccination_token = client.post("/api/queue/register", json={
        "branch_id": branch_id,
        "full_name": "Vaccination Patient",
        "mobile": "0771234588",
        "service_type_id": vaccination_id,
    })
    assert vaccination_token.status_code == 201

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

    assert client.post(
        "/api/auth/login",
        json={"email": "reception@example.com", "password": "secret123"},
    ).status_code == 200
    queue_response = client.get("/api/queue/waiting")
    assert queue_response.status_code == 200
    assert [token["service"] for token in queue_response.get_json()] == ["Vaccination"]


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
