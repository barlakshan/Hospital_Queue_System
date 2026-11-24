"""Shared test fixtures.

The autouse guard below makes it impossible for any test to reach a real SMS
gateway, even if a future config change stops TestingConfig forcing "mock".
A test that needs the real dispatch code (and mocks `requests` itself) can
opt out with @pytest.mark.real_sms_dispatch.
"""
import pytest

from app.services.sms_service import SmsService


@pytest.fixture(autouse=True)
def _no_real_sms(request, monkeypatch):
    sent = []
    if request.node.get_closest_marker("real_sms_dispatch"):
        return sent

    def fake_dispatch(cls, mobile, message):
        sent.append((mobile, message))
        return {"status": "sent", "message_id": "test"}

    monkeypatch.setattr(SmsService, "_dispatch", classmethod(fake_dispatch))
    return sent


@pytest.fixture(autouse=True)
def _fresh_login_throttle():
    from app.api.auth import reset_login_throttle
    reset_login_throttle()
    yield
    reset_login_throttle()
