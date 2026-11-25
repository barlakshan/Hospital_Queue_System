import pytest
from unittest.mock import Mock, patch

from app import create_app
from app.services.sms_service import SmsService


@pytest.mark.parametrize("value", ["textlk", "Text.lk", "text_lk",
                                   "https://app.text.lk/api/http/sms/send"])
def test_sms_provider_accepts_textlk_aliases(value):
    from app.config import _normalize_sms_provider
    assert _normalize_sms_provider(value) == "textlk"


def test_testing_config_never_uses_a_real_gateway(monkeypatch):
    # Config is read at import time, so the class attribute is what matters.
    from app.config import TestingConfig
    assert TestingConfig.SMS_PROVIDER == "mock"
    assert create_app("testing").config["SMS_PROVIDER"] == "mock"


@pytest.mark.real_sms_dispatch
def test_textlk_uses_http_sms_send_contract(monkeypatch):
    app = create_app("testing")
    app.config.update(
        SMS_PROVIDER="textlk",
        TEXTLK_API_TOKEN="test-token",
        TEXTLK_SENDER_ID="TextLKDemo",
    )
    response = Mock()
    response.status_code = 200
    response.text = '{"status":"success","data":{"id":"sms-123"}}'
    response.json.return_value = {
        "status": "success",
        "data": {"id": "sms-123"},
    }

    with app.app_context(), patch(
        "app.services.sms_service.requests.post",
        return_value=response,
    ) as post:
        result = SmsService._dispatch("0771234567", "Your queue status")

    post.assert_called_once_with(
        "https://app.text.lk/api/http/sms/send",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "api_token": "test-token",
            "recipient": "94771234567",
            "sender_id": "TextLKDemo",
            "type": "plain",
            "message": "Your queue status",
        },
        timeout=10,
    )
    assert result == {"status": "success", "message_id": "sms-123"}


def _fake_token(wait, phone="0112345678"):
    from types import SimpleNamespace
    return SimpleNamespace(
        display_name="Nimal Perera",
        token_number="G005",
        public_ref="abc123",
        estimated_wait_min=wait,
        service_type=SimpleNamespace(name="General OPD", department=None),
        branch=SimpleNamespace(name="Colombo", phone=phone),
        counter=None,
        served_by_officer=None,
    )


def _local(hour, minute):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 11, 24, hour, minute, tzinfo=ZoneInfo("Asia/Colombo"))


def test_confirmation_sms_asks_patient_to_arrive_20_min_early():
    app = create_app("testing", BASE_URL="https://queue.example.com/")
    with app.app_context():
        msg = SmsService.build_confirmation(_fake_token(wait=60), position=6,
                                            now=_local(9, 0))
    assert "Queue No: G005" in msg
    assert "Dear Nimal Perera" in msg
    assert "Service: General OPD" in msg
    assert "Location: Colombo" in msg
    assert "Date: 24 Nov 2026" in msg
    assert "Patients ahead: 6" in msg
    assert "Your turn approx: 10:00 AM" in msg
    assert "reach the hospital by 9:40 AM" in msg
    assert "Live status: https://queue.example.com/register/token/abc123" in msg
    assert "Help: 0112345678" in msg
    # GSM-7 only, so the SMS is not billed as Unicode.
    assert msg.isascii()


def test_confirmation_sms_short_wait_asks_patient_to_come_now():
    app = create_app("testing", BASE_URL="")
    with app.app_context():
        msg = SmsService.build_confirmation(_fake_token(wait=15, phone=None),
                                            position=1, now=_local(9, 0))
    assert "Please reach the hospital now" in msg
    assert "Live status" not in msg
    assert "Help:" not in msg


def test_arrive_early_minutes_is_configurable():
    app = create_app("testing", SMS_ARRIVE_EARLY_MIN=30)
    with app.app_context():
        msg = SmsService.build_confirmation(_fake_token(wait=60), position=6,
                                            now=_local(9, 0))
    assert "reach the hospital by 9:30 AM" in msg


def test_call_notification_sms():
    from types import SimpleNamespace
    token = _fake_token(wait=0)
    token.counter = SimpleNamespace(label="Room 3")
    token.served_by_officer = SimpleNamespace(full_name="Dr. Silva")
    app = create_app("testing")
    with app.app_context():
        msg = SmsService.build_call_notification(token)
    assert "Queue No: G005" in msg
    assert "Please proceed to General OPD (Room 3)." in msg
    assert "Dr. Silva" not in msg


def test_call_notification_without_counter_names_the_department():
    app = create_app("testing")
    with app.app_context():
        msg = SmsService.build_call_notification(_fake_token(wait=0))
    assert "Please proceed to General OPD.\n" in msg
    assert "counter" not in msg.lower()
    assert "Staff:" not in msg
    assert msg.isascii()


@pytest.mark.real_sms_dispatch
def test_unknown_provider_fails_instead_of_silently_mocking():
    app = create_app("testing", SMS_PROVIDER="txtlk")
    with app.app_context(), pytest.raises(RuntimeError, match="Unknown SMS_PROVIDER"):
        SmsService._dispatch("0771234567", "hello")


def test_production_refuses_unknown_sms_provider():
    from app import _check_production_config
    app = create_app("testing", SMS_PROVIDER="txtlk",
                     SECRET_KEY="x" * 40, SQLALCHEMY_DATABASE_URI="sqlite://")
    with pytest.raises(RuntimeError, match="SMS_PROVIDER"):
        _check_production_config(app)


def test_call_notification_names_department_and_service():
    from types import SimpleNamespace
    token = _fake_token(wait=0)
    token.service_type = SimpleNamespace(name="Dental", department="Specialist Consultation")
    app = create_app("testing")
    with app.app_context():
        msg = SmsService.build_call_notification(token)
    assert "Please proceed to Specialist Consultation - Dental." in msg
