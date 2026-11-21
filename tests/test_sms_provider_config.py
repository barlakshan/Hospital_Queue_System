import pytest
from unittest.mock import Mock, patch

from app import create_app
from app.services.sms_service import SmsService


def test_sms_provider_accepts_textlk_alias(monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "textlk")
    monkeypatch.setenv("TEXTLK_API_TOKEN", "test-token")

    app = create_app("testing")

    assert app.config["SMS_PROVIDER"] == "textlk"


def test_sms_provider_accepts_textlk_url(monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "https://app.text.lk/api/http/sms/send")
    monkeypatch.setenv("TEXTLK_API_TOKEN", "test-token")

    app = create_app("testing")

    assert app.config["SMS_PROVIDER"] == "textlk"


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
