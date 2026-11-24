import logging

from app.extensions import db
from app.models import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    def log(user_id, action: str, payload: dict = None, token_id: int = None):
        """Write one audit row in its own commit; never raises."""
        try:
            from flask import request
            ip = request.remote_addr if request else None
        except RuntimeError:
            ip = None

        if token_id is not None:
            from app.models import QueueToken
            if db.session.get(QueueToken, token_id) is None:
                # Keep the attempted id without violating the foreign key
                # (e.g. a rejected request for a token that doesn't exist).
                payload = {**(payload or {}), "token_id": token_id}
                token_id = None

        entry = AuditLog(
            user_id=user_id,
            token_id=token_id,
            action=action,
            payload=payload or {},
            ip_address=ip,
        )
        try:
            db.session.add(entry)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("AuditService.log failed: %s", e)

    @staticmethod
    def diff(before: dict, after: dict) -> dict:
        """{field: [old, new]} for fields whose value changed."""
        return {k: [before.get(k), after.get(k)]
                for k in after if before.get(k) != after.get(k)}
