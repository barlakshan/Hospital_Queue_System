import sys
sys.path.insert(0, '.')

from app import create_app
from app.extensions import db
from app.models import Branch, User, UserRole, ServiceType

app = create_app('development')

with app.app_context():
    # Seed hospital services
    if ServiceType.query.count() == 0:
        services = [
            ServiceType(code="G", name="General OPD", prefix="G", avg_service_min=10,
                        allowed_roles=["lobby_officer"]),
            ServiceType(code="S", name="Specialist Consultation", prefix="S", avg_service_min=15,
                        allowed_roles=["lobby_officer", "loan_officer"]),
            ServiceType(code="L", name="Laboratory", prefix="L", avg_service_min=8,
                        allowed_roles=["loan_officer"]),
            ServiceType(code="R", name="Radiology", prefix="R", avg_service_min=12,
                        allowed_roles=["loan_officer"]),
            ServiceType(code="P", name="Pharmacy", prefix="P", avg_service_min=6,
                        allowed_roles=["lobby_officer", "loan_officer"]),
            ServiceType(code="B", name="Billing / Cashier", prefix="B", avg_service_min=5,
                        allowed_roles=["lobby_officer", "loan_officer"]),
        ]
        for s in services:
            db.session.add(s)
        db.session.commit()
        print("Service types seeded")

    # Create a test hospital location
    branch = Branch.query.filter_by(code='TEST').first()
    if not branch:
        branch = Branch(code='TEST', name='Test Hospital', is_active=True)
        db.session.add(branch)
        db.session.commit()
        print(f"Created branch: {branch.id}")

    # Create a test manager
    manager = User.query.filter_by(email='testmanager@test.com').first()
    if not manager:
        manager = User(
            employee_id='MGR001',
            full_name='Test Manager',
            email='testmanager@test.com',
            role=UserRole.BRANCH_MANAGER,
            branch_id=branch.id,
        )
        manager.set_password('test123')
        db.session.add(manager)
        db.session.commit()
        print(f"Created manager: {manager.id}")
    else:
        print(f"Manager already exists: {manager.id}")

print("Setup complete!")
