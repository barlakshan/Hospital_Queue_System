"""UAT: staff console, waiting-area display and the full patient visit."""
import pytest
from selenium.webdriver.common.by import By

from app.extensions import db
from app.models import AppointmentRequest, Branch, ServiceType
from uat_helpers import button

pytestmark = pytest.mark.uat

CALL_NEXT = "#btn-call-next"


def _signed_in_officer(open_browser):
    staff = open_browser()
    staff.sign_in("opd@uat.test")
    staff.path_is("/officer/dashboard")
    return staff


def test_stf_01_full_visit_from_registration_to_rating(open_browser):
    patient = open_browser(mobile=True)
    token = patient.register_patient("Nimal Perera", "0771234567")

    staff = _signed_in_officer(open_browser)
    staff.see_text(token, "#queue-list")
    staff.click(CALL_NEXT)
    staff.see_text(token, "#serving-section")

    patient.click("#refresh-btn")
    patient.see_text("Called!", "#status-badge")
    patient.see_text("Room 1", "#call-location")

    # A reload must resume the same patient, not lose them.
    staff.driver.refresh()
    staff.see_text(token, "#serving-section")

    staff.click("#btn-start-service")
    staff.see_text("Consultation started")
    staff.fill("#serving-section textarea", "Fever, advised rest")
    staff.click(button("Mark complete"))
    staff.see_text("Service completed")
    staff.see_text("No patient being seen", "#serving-section")

    patient.click("#refresh-btn")
    patient.see_text("Completed", "#status-badge")
    patient.click(".star[data-v='5']")
    patient.fill("#rating-comment", "Quick and friendly")
    patient.click("#submit-rating")
    patient.see_text("Thank you for your feedback", "#rating-done")


def test_stf_02_visit_cannot_be_completed_without_a_summary(open_browser):
    token = open_browser(mobile=True).register_patient("Nimal Perera", "0771234567")
    staff = _signed_in_officer(open_browser)
    staff.click(CALL_NEXT)
    staff.see_text(token, "#serving-section")
    staff.click("#btn-start-service")
    staff.see_text("Consultation started")

    staff.click(button("Mark complete"))

    staff.see_text("Enter a brief condition summary")
    staff.see_text(token, "#serving-section")


def test_stf_03_confirmed_priority_patient_is_called_first(open_browser):
    patient = open_browser(mobile=True)
    regular = patient.register_patient("Regular Patient", "0771234567")
    elderly = patient.register_patient("Elderly Patient", "0771234568", priority="Elderly")

    staff = _signed_in_officer(open_browser)
    staff.click(button("Confirm"))
    staff.see_text("Priority set")
    staff.wait(
        lambda d: d.find_element(By.CSS_SELECTOR, "#queue-list .pb-queue-token").text == elderly,
        "priority patient did not move to the top of the queue",
    )

    staff.click(CALL_NEXT)

    staff.see_text(elderly, "#serving-section")
    assert regular not in staff.text_of("#serving-section")


def test_stf_04_absent_patient_is_marked_no_show(open_browser):
    patient = open_browser(mobile=True)
    token = patient.register_patient("Nimal Perera", "0771234567")
    staff = _signed_in_officer(open_browser)
    staff.click(CALL_NEXT)
    staff.see_text(token, "#serving-section")

    staff.click(button("No-show"))

    staff.see_text("Marked as no-show")
    staff.see_text("No patient being seen", "#serving-section")


def test_stf_05_display_screen_shows_the_called_token_without_the_name(open_browser):
    token = open_browser(mobile=True).register_patient("Nimal Perera", "0771234567")
    display = open_browser()
    display.go("/display/UAT")
    display.see_text(token, "#d-next-badges")

    staff = _signed_in_officer(open_browser)
    staff.click(CALL_NEXT)

    display.see_text(token, "#d-token")
    display.see_text("Room 1", "#d-counter")
    assert "Nimal" not in display.driver.page_source


def test_stf_06_staff_schedules_an_appointment_request(site, open_browser):
    def add_request():
        db.session.add(AppointmentRequest(
            branch_id=Branch.query.filter_by(code="UAT").one().id,
            service_type_id=ServiceType.query.filter_by(code="G").one().id,
            full_name="Sunil Fernando", mobile="+94771234567",
        ))
        db.session.commit()
    site.query(add_request)

    staff = _signed_in_officer(open_browser)
    staff.see_text("Sunil Fernando")
    staff.click(button("Schedule"))

    staff.see_text("Appointment request marked scheduled")
    assert site.query(lambda: AppointmentRequest.query.one().status) == "scheduled"
