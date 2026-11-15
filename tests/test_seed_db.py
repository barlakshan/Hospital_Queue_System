from click.testing import CliRunner

from app import create_app
from app.extensions import db
from app.models import ServiceType


def test_seed_db_creates_schema_and_inserts_service_types():
    app = create_app("testing")
    runner = CliRunner()

    with app.app_context():
        db.drop_all()

    result = runner.invoke(app.cli, ["seed-db"])

    assert result.exit_code == 0, result.output
    assert "Service types seeded" in result.output

    with app.app_context():
        active_services = ServiceType.query.filter_by(is_active=True).all()
        assert len(active_services) == 6
        assert {service.name for service in active_services} == {
            "General OPD",
            "Specialist Consultation",
            "Laboratory",
            "Radiology",
            "Pharmacy",
            "Billing / Cashier",
        }
