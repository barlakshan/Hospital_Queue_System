from app import create_app
from app.extensions import db
from app.models import User, UserRole


def _app_with_admin():
    app = create_app("testing")
    with app.app_context():
        db.drop_all()
        db.create_all()
        u = User(employee_id="A1", full_name="Admin", email="admin@example.com",
                 role=UserRole.ADMIN, is_active=False, must_change_password=True)
        u.set_password("old-password")
        db.session.add(u)
        db.session.commit()
    return app


def test_reset_password_sets_password_and_reactivates():
    app = _app_with_admin()
    runner = app.test_cli_runner()

    result = runner.invoke(args=["reset-password", "--email", " Admin@Example.com ",
                                 "--password", "new-password-123"])
    assert result.exit_code == 0, result.output

    with app.app_context():
        u = User.query.filter_by(email="admin@example.com").one()
        assert u.check_password("new-password-123")
        assert not u.check_password("old-password")
        assert u.is_active and not u.must_change_password

    listed = runner.invoke(args=["list-admins"])
    assert "admin@example.com" in listed.output and "active" in listed.output


def test_reset_password_rejects_unknown_email_and_weak_password():
    app = _app_with_admin()
    runner = app.test_cli_runner()

    missing = runner.invoke(args=["reset-password", "--email", "nobody@example.com",
                                  "--password", "new-password-123"])
    assert missing.exit_code != 0 and "No user" in missing.output

    weak = runner.invoke(args=["reset-password", "--email", "admin@example.com",
                               "--password", "short"])
    assert weak.exit_code != 0
    with app.app_context():
        assert User.query.filter_by(email="admin@example.com").one().check_password("old-password")
