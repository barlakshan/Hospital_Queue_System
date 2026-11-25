"""UAT: sign-in rules, administrator tasks and the location manager."""
from pathlib import Path

import pytest
from selenium.webdriver.common.by import By

from app.models import Branch
from uat_helpers import PASSWORD, button

pytestmark = pytest.mark.uat

QR_DIR = Path(__file__).resolve().parents[2] / "static" / "qrcodes"


def _signed_in_admin(open_browser):
    admin = open_browser()
    admin.sign_in("admin@uat.test")
    admin.path_is("/admin/dashboard")
    return admin


def test_auth_01_wrong_password_is_refused(open_browser):
    browser = open_browser()

    browser.sign_in("admin@uat.test", "not-the-password")

    browser.see_text("Invalid email or password")
    browser.path_is("/auth/login")


@pytest.mark.parametrize("email, home", [
    ("admin@uat.test", "/admin/dashboard"),
    ("manager@uat.test", "/manager/dashboard"),
    ("opd@uat.test", "/officer/dashboard"),
])
def test_auth_02_each_role_lands_on_its_own_dashboard(open_browser, email, home):
    browser = open_browser()

    browser.sign_in(email)

    browser.path_is(home)


def test_auth_03_signing_out_closes_the_session(open_browser):
    staff = open_browser()
    staff.sign_in("opd@uat.test")

    staff.click((By.XPATH, "//button[contains(normalize-space(),'Sign out')]"))
    staff.see_text("You have been signed out")
    staff.go("/officer/dashboard")

    staff.path_is("/auth/login")


def test_auth_04_staff_cannot_open_admin_pages(open_browser):
    staff = open_browser()
    staff.sign_in("opd@uat.test")
    staff.path_is("/officer/dashboard")

    staff.go("/admin/dashboard")

    staff.see_text("Forbidden")


def test_adm_01_admin_adds_a_hospital_location(site, open_browser):
    # Creating a location also writes its QR image into static/qrcodes/.
    qr_image = QR_DIR / "UATNEW.png"
    admin = _signed_in_admin(open_browser)
    admin.go("/admin/branches/new")
    try:
        admin.fill("#code", "UATNEW")
        admin.fill("#name", "Kandy Clinic")
        admin.click(button("Create branch"))

        admin.wait(lambda d: not d.current_url.endswith("/new"), "form was not submitted")
        admin.go("/admin/branches")
        admin.see_text("Kandy Clinic")
        assert site.query(lambda: Branch.query.filter_by(code="UATNEW").count()) == 1
    finally:
        qr_image.unlink(missing_ok=True)


def test_adm_02_new_staff_account_must_choose_its_own_password(open_browser):
    admin = _signed_in_admin(open_browser)
    admin.go("/admin/users/new")
    admin.fill("#employee_id", "OPD002")
    admin.fill("#full_name", "New Officer")
    admin.fill("#email", "new.officer@uat.test")
    admin.fill("#password", PASSWORD)
    admin.choose("#role", "OPD")
    admin.choose("#branch_id", "UAT Hospital")
    admin.click((By.XPATH, "//label[contains(normalize-space(),'General OPD')]/input"))
    admin.click(button("Create user"))
    admin.see_text("New Officer")

    officer = open_browser()
    officer.sign_in("new.officer@uat.test")
    officer.path_is("/auth/password")
    officer.fill("#current_password", PASSWORD)
    officer.fill("#new_password", "My-Own-Pass-456")
    officer.fill("#confirm_password", "My-Own-Pass-456")
    officer.click(button("Change password"))

    officer.see_text("Password changed")
    officer.path_is("/officer/dashboard")


def test_adm_03_new_service_appears_on_the_patient_form(open_browser):
    admin = _signed_in_admin(open_browser)
    admin.go("/admin/services")
    admin.fill("#new-name", "Physiotherapy")
    admin.click((By.XPATH, "//button[contains(normalize-space(),'Add service')]"))
    admin.see_text("Service added successfully")

    patient = open_browser(mobile=True)
    patient.go("/register/UAT")

    patient.see_text("Physiotherapy", "#service-type")


@pytest.mark.parametrize("path, heading", [
    ("/manager/dashboard", "UAT Hospital"),
    ("/manager/analytics", "Analytics"),
    ("/manager/officers", "Uat Officer"),
    ("/manager/customers", "Patients"),
])
def test_mgr_01_manager_pages_open(open_browser, path, heading):
    manager = open_browser()
    manager.sign_in("manager@uat.test")
    manager.path_is("/manager/dashboard")

    manager.go(path)

    manager.see_text(heading)
