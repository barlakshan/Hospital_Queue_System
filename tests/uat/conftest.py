"""Fixtures for the Selenium UAT suite.

Every test gets a fresh SQLite database, its own app server and its own
browser(s), so tests do not depend on each other or on developer data.

Environment variables:
  UAT_HEADED=1    show the browser window (for a demo or sign-off session)
  UAT_SLOWMO=0.5  pause this many seconds after each action
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from selenium import webdriver

from app.extensions import db
from app.models import Branch, Counter, ServiceType, User, UserRole
from uat_helpers import PASSWORD, Browser
from uat_server import uat_app

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARTIFACTS = ROOT / "uat-artifacts"


def _seed(app):
    with app.app_context():
        branch = Branch(code="UAT", name="UAT Hospital", city="Colombo", is_active=True)
        opd = ServiceType(code="G", name="General OPD", prefix="G",
                          avg_service_min=10, allowed_roles=["opd"])
        lab = ServiceType(code="L", name="Laboratory", prefix="L",
                          avg_service_min=8, allowed_roles=["pharmacy"])
        db.session.add_all([branch, opd, lab])
        db.session.flush()

        def user(employee_id, name, email, role, **extra):
            account = User(employee_id=employee_id, full_name=name,
                           email=email, role=role, **extra)
            account.set_password(PASSWORD)
            db.session.add(account)
            return account

        user("ADM001", "Uat Admin", "admin@uat.test", UserRole.ADMIN)
        user("MGR001", "Uat Manager", "manager@uat.test",
             UserRole.BRANCH_MANAGER, branch_id=branch.id)
        staff = user("OPD001", "Uat Officer", "opd@uat.test", UserRole.OPD,
                     branch_id=branch.id, assigned_services=[opd])
        db.session.flush()
        db.session.add(Counter(branch_id=branch.id, label="Room 1", user_id=staff.id))
        db.session.commit()


class UatSite:
    def __init__(self, base_url, app):
        self.base_url = base_url
        self.app = app

    def query(self, func):
        """Run a database check against the server's data."""
        with self.app.app_context():
            return func()


@pytest.fixture()
def site(tmp_path):
    database_uri = f"sqlite:///{tmp_path / 'uat.sqlite3'}"
    app = uat_app(database_uri)
    _seed(app)
    with open(tmp_path / "server.log", "w") as log:
        process = subprocess.Popen(
            [sys.executable, str(HERE / "uat_server.py"), database_uri],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=log, text=True,
        )
        try:
            port = process.stdout.readline().strip()
            if not port:
                pytest.fail((tmp_path / "server.log").read_text())
            yield UatSite(f"http://127.0.0.1:{port}", app)
        finally:
            process.terminate()
            process.wait(timeout=10)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item.uat_failed = report.failed


@pytest.fixture()
def open_browser(site, request):
    """Factory: each call opens an independent browser (its own session)."""
    drivers = []

    def _open(mobile=False):
        options = webdriver.ChromeOptions()
        if os.environ.get("UAT_HEADED") != "1":
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=" + ("420,900" if mobile else "1400,1000"))
        driver = webdriver.Chrome(options=options)
        drivers.append(driver)
        return Browser(driver, site.base_url,
                       slowmo=float(os.environ.get("UAT_SLOWMO", "0")))

    yield _open

    if getattr(request.node, "uat_failed", False):
        ARTIFACTS.mkdir(exist_ok=True)
        name = re.sub(r"[^\w.-]+", "_", request.node.name)
        for index, driver in enumerate(drivers, start=1):
            driver.save_screenshot(str(ARTIFACTS / f"{name}-browser{index}.png"))
    for driver in drivers:
        driver.quit()
