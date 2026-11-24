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
