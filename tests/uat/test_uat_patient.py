"""UAT: patient-facing pages (no sign-in)."""
import pytest

from app.models import AppointmentRequest, QueueToken

pytestmark = pytest.mark.uat


def test_pat_01_patient_registers_and_gets_a_token(open_browser):
    patient = open_browser(mobile=True)

    token = patient.register_patient("Nimal Perera", "0771234567")

    assert token.startswith("G")
    patient.see_text("GENERAL OPD", "#ticket-card")  # upper-cased by CSS
    patient.see_text("Waiting", "#status-badge")
    patient.see_text("67")  # last two digits of the masked mobile
    assert "0771234567" not in patient.driver.page_source


def test_pat_02_registering_twice_returns_the_same_token(site, open_browser):
    patient = open_browser(mobile=True)

    first = patient.register_patient("Nimal Perera", "0771234567")
    second = patient.register_patient("Nimal Perera", "0771234567")

    assert second == first
    assert site.query(lambda: QueueToken.query.count()) == 1


def test_pat_03_priority_is_only_a_request_until_staff_confirm(open_browser):
    patient = open_browser(mobile=True)

    patient.register_patient("Kamala Silva", "0771234568", priority="Elderly")

    patient.see_text("Priority requested (Elderly)", "#ticket-card")


def test_pat_04_invalid_mobile_number_is_rejected(site, open_browser):
    patient = open_browser(mobile=True)

    patient.submit_registration("Nimal Perera", "12345")

    assert "mobile" in patient.accept_alert().lower()
    patient.path_is("/register/UAT")
    assert site.query(lambda: QueueToken.query.count()) == 0


def test_pat_05_patient_requests_an_appointment(site, open_browser):
    patient = open_browser(mobile=True)
    patient.go("/appointments/UAT")

    patient.fill("#full-name", "Nimal Perera")
    patient.fill("#mobile", "0771234567")
    patient.choose("#service-type", "General OPD")
    patient.click("#submit-btn")

    patient.wait(lambda d: site.query(lambda: AppointmentRequest.query.count()) == 1,
                 "the request was not saved")
    patient.see_text("appointment request was sent", "#request-message")
