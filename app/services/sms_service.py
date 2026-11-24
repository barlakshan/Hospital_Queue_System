import logging
import threading
import time

import requests
from flask import current_app

from app.utils.validators import mask_mobile, normalise_mobile

logger = logging.getLogger(__name__)


class SmsService:
    """
    SMS abstraction layer.
    Set SMS_PROVIDER in .env:  mock | textlk | dialog | mobitel | twilio

    Messages are sent from a background thread and every attempt is recorded
    in sms_logs. `flask retry-sms` (run from cron) re-sends recent failures.
    """

    KNOWN_PROVIDERS = ("mock", "textlk", "dialog", "mobitel", "twilio")

    # Plain GSM-7 characters only (no emojis, curly quotes or long dashes):
    # a single non-GSM character switches the whole SMS to Unicode, which
    # cuts each segment from 160 to 70 characters and multiplies the cost.
    BRAND = "Melsta Hospitals"
    TEMPLATES = {
        "confirmation": (
            "{brand}\n"
            "Dear {name}, thank you for choosing us!\n"
            "Queue No: {token}\n"
            "Service: {service}\n"
            "Location: {branch}\n"
            "Date: {date}\n"
            "Patients ahead: {ahead}\n"
            "Your turn approx: {call_time}\n"
            "{arrival}"
            "{track}"
            "{contact}"
            "Your health, our priority."
        ),
        "arrive_by": (
            "Please reach the hospital by {arrive_by}, "
            "at least {early} min before your turn.\n"
        ),
        "arrive_now": (
            "Your turn is near. Please reach the hospital now "
            "(at least {early} min before your turn).\n"
        ),
        "track":   "Live status: {url}\n",
        "contact": "Help: {phone}\n",
        "call_notification": (
            "{brand}\n"
            "Dear {name}, it is your turn now!\n"
            "Queue No: {token}\n"
            "Please proceed to {department}{counter}.\n"
            "{officer}"
            "Location: {branch}\n"
            "Thank you for your patience."
        ),
    }

    @staticmethod
    def _clock(dt):
        """'9:05 AM' style local time."""
        return dt.strftime("%I:%M %p").lstrip("0")

    @staticmethod
    def _token_url(token):
        """Public live-status page for the token, or "" if no public URL is known."""
        from flask import has_request_context, url_for
        base = (current_app.config.get("BASE_URL") or "").rstrip("/")
        path = f"/register/token/{token.public_ref}"
        if base:
            return base + path
        if has_request_context():
            return url_for("customer.token_page", token_ref=token.public_ref, _external=True)
        return ""

    @classmethod
    def build_confirmation(cls, token, position=None, now=None):
        """Registration SMS: queue number, visit details and the time the
        patient must be at the hospital (SMS_ARRIVE_EARLY_MIN before their
        estimated turn)."""
        from datetime import timedelta
        from app.services.queue_service import QueueService
        from app.utils.timeutil import local_now, to_local

        now = to_local(now) if now else local_now()
        if position is None:
            position = QueueService.get_position(token)
        wait = token.estimated_wait_min or 0
        early = int(current_app.config.get("SMS_ARRIVE_EARLY_MIN", 20))
        call_at = now + timedelta(minutes=wait)
        arrive_by = call_at - timedelta(minutes=early)

        if arrive_by > now:
            arrival = cls.TEMPLATES["arrive_by"].format(
                arrive_by=cls._clock(arrive_by), early=early)
        else:
            arrival = cls.TEMPLATES["arrive_now"].format(early=early)

        url = cls._token_url(token)
        phone = (token.branch.phone or "").strip()
        return cls.TEMPLATES["confirmation"].format(
            brand=cls.BRAND,
            name=token.display_name or "Patient",
            token=token.token_number,
            service=token.service_type.name,
            branch=token.branch.name,
            date=now.strftime("%d %b %Y"),
            ahead=position,
            call_time=cls._clock(call_at),
            arrival=arrival,
            track=cls.TEMPLATES["track"].format(url=url) if url else "",
            contact=cls.TEMPLATES["contact"].format(phone=phone) if phone else "",
        )

    @classmethod
    def build_call_notification(cls, token):
        counter = token.counter.label if token.counter else ""
        officer = token.served_by_officer.full_name if token.served_by_officer else ""
        return cls.TEMPLATES["call_notification"].format(
            brand=cls.BRAND,
            name=token.display_name or "Patient",
            token=token.token_number,
            department=token.service_type.name,
            counter=f" ({counter})" if counter else "",
            officer=f"Staff: {officer}\n" if officer else "",
            branch=token.branch.name,
        )

    @classmethod
    def send_confirmation(cls, token):
        cls._send_async(
            mobile=token.customer.mobile,
            message=cls.build_confirmation(token),
            token_id=token.id,
            sms_type="confirmation",
        )

    @classmethod
    def send_call_notification(cls, token):
        message = cls.build_call_notification(token)
        cls._send_async(
            mobile=token.customer.mobile,
            message=message,
            token_id=token.id,
            sms_type="call_notification",
        )

    @classmethod
    def _send_async(cls, mobile, message, token_id, sms_type):
        """Send from a daemon thread so the request never waits on the gateway."""
        app = current_app._get_current_object()
        if app.config.get("SMS_SEND_INLINE"):
            cls._thread_send(app, mobile, message, token_id, sms_type)
            return
        try:
            threading.Thread(
                target=cls._thread_send,
                args=(app, mobile, message, token_id, sms_type),
                daemon=True,
            ).start()
        except Exception:
            logger.exception("Failed to start SMS thread for token %s", token_id)

    @classmethod
    def _thread_send(cls, app, mobile, message, token_id, sms_type):
        with app.app_context():
            from app.extensions import db
            from app.models import SmsLog, SmsType, SmsStatus

            log = SmsLog(
                token_id=token_id,
                sms_type=SmsType[sms_type.upper()],
                mobile=mobile,
                message=message,
                status=SmsStatus.PENDING,
            )
            db.session.add(log)
            db.session.commit()
            cls._attempt(log)
            db.session.remove()

    @classmethod
    def _attempt(cls, log):
        """Dispatch one SmsLog row and record the outcome."""
        from app.extensions import db
        from app.models import SmsStatus
        from app.utils.timeutil import utcnow

        try:
            result = cls._dispatch(log.mobile, log.message)
            log.status              = SmsStatus.SENT
            log.provider_message_id = str(result.get("message_id", ""))[:80]
            log.sent_at             = utcnow()
            logger.info("SMS sent to %s type=%s id=%s",
                        mask_mobile(log.mobile), log.sms_type.value, log.provider_message_id)
        except Exception as exc:
            log.status = SmsStatus.FAILED
            logger.error("SMS send failed to %s type=%s: %s",
                         mask_mobile(log.mobile), log.sms_type.value, exc)
        finally:
            try:
                db.session.commit()
            except Exception:
                logger.exception("Failed to update SMS log %s", log.id)
                db.session.rollback()
        return log.status

    @classmethod
    def retry_failed(cls, max_age_minutes=10):
        """Re-send FAILED messages created in the last `max_age_minutes`."""
        from datetime import timedelta
        from app.models import SmsLog, SmsStatus
        from app.utils.timeutil import utcnow

        cutoff = (utcnow() - timedelta(minutes=max_age_minutes)).replace(tzinfo=None)
        failed = (SmsLog.query
                  .filter(SmsLog.status == SmsStatus.FAILED, SmsLog.created_at >= cutoff)
                  .order_by(SmsLog.id).all())
        sent = sum(1 for log in failed if cls._attempt(log) == SmsStatus.SENT)
        return len(failed), sent

    @classmethod
    def _dispatch(cls, mobile: str, message: str) -> dict:
        provider = current_app.config.get("SMS_PROVIDER", "mock")

        if provider == "dialog":
            return cls._dialog(mobile, message)
        elif provider == "mobitel":
            return cls._mobitel(mobile, message)
        elif provider == "textlk":
            return cls._textlk(mobile, message)
        elif provider == "twilio":
            return cls._twilio(mobile, message)
        elif provider != "mock":
            # A typo in SMS_PROVIDER must not silently stop all SMS.
            raise RuntimeError(f"Unknown SMS_PROVIDER {provider!r}")
        else:
            # Message bodies contain patient details: only log them in
            # development.
            if current_app.debug:
                logger.info("[SMS MOCK] To:%s | %s", mask_mobile(mobile), message)
            else:
                logger.warning("[SMS MOCK] SMS_PROVIDER is mock: nothing sent to %s",
                               mask_mobile(mobile))
            return {"status": "sent", "message_id": "mock-001"}

    @classmethod
    def _dialog(cls, mobile: str, message: str) -> dict:
        m = mobile.replace("+", "").replace(" ", "")
        resp = requests.post(
            "https://sms.dialog.lk/api/send",
            json={
                "user": current_app.config["DIALOG_USER"],
                "pass": current_app.config["DIALOG_PASS"],
                "msisdn": m,
                "msg":    message,
                "sid":    current_app.config["DIALOG_SENDER_ID"],
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    @classmethod
    def _mobitel(cls, mobile: str, message: str) -> dict:
        resp = requests.post(
            "https://api.mobitel.lk/sms/send",  # confirm with Mobitel
            headers={
                "Authorization": f"Bearer {current_app.config['MOBITEL_TOKEN']}",
                "Content-Type":  "application/json",
            },
            json={
                "to":      mobile,
                "message": message,
                "from":    current_app.config.get("MOBITEL_SENDER_ID", "MELSTAHOSP"),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    @classmethod
    def _textlk(cls, mobile: str, message: str) -> dict:
        """
        Send SMS using Text.lk API: https://app.text.lk/api/http/sms/send
        Expects `TEXTLK_API_TOKEN` in app config and optional `TEXTLK_SENDER_ID`.
        Returns a dict with at least `message_id` when available.
        """
        token = current_app.config.get("TEXTLK_API_TOKEN")
        if not token:
            raise RuntimeError("TEXTLK_API_TOKEN not configured")

        sender = current_app.config.get("TEXTLK_SENDER_ID", "MELSTAHOSP")

        # Text.lk expects 94771234567 (no +)
        recipient = mobile.replace("+", "").replace(" ", "").replace("-", "")
        if recipient.startswith("0"):
            recipient = "94" + recipient[1:]

        try:
            resp = requests.post(
                "https://app.text.lk/api/http/sms/send",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "api_token": token,
                    "recipient": recipient,
                    "sender_id": sender,
                    "type": "plain",
                    "message": message,
                },
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Text.lk API request failed: {e.__class__.__name__}") from e

        logger.debug("Text.lk API response status: %s", resp.status_code)
        if resp.status_code >= 400:
            logger.error("Text.lk API error %s: %s", resp.status_code, resp.text[:200])
            raise RuntimeError(f"Text.lk API HTTP {resp.status_code}")

        data = resp.json()
        message_id = ""
        status = "unknown"
        if isinstance(data, dict):
            status = data.get("status", data.get("code", "unknown"))
            if status in ("success", "200", 200):
                d = data.get("data")
                if isinstance(d, dict):
                    message_id = d.get("uid") or d.get("id") or d.get("message_id") or ""
                elif isinstance(d, str):
                    message_id = d
                if not message_id:
                    message_id = (data.get("message_id") or data.get("uid")
                                  or data.get("id") or f"textlk-{int(time.time() * 1000)}")
            else:
                error_msg = data.get("message", data.get("error", "unknown error"))
                raise RuntimeError(f"Text.lk API error: {error_msg}")

        return {"status": str(status), "message_id": message_id}

    @classmethod
    def _twilio(cls, mobile: str, message: str) -> dict:
        from twilio.rest import Client
        client = Client(
            current_app.config["TWILIO_SID"],
            current_app.config["TWILIO_TOKEN"],
        )
        msg = client.messages.create(
            body=message,
            from_=current_app.config["TWILIO_FROM"],
            to=mobile,
        )
        return {"message_id": msg.sid}

    # Kept as an alias: callers historically used SmsService.normalise_mobile.
    normalise_mobile = staticmethod(normalise_mobile)
