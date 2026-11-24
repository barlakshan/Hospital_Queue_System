import logging

from app.extensions import socketio
from app.models import ACTIVE_STATUSES, QueueToken, TokenStatus
from app.utils.timeutil import local_today

logger = logging.getLogger(__name__)


def build_display_state(branch) -> dict:
    """Public queue state for a location. The REST endpoint and the live
    Socket.IO push both use this, so the ordering is always the same.

    Only display-level data belongs here: no patient names or priority
    reasons.
    """
    today = local_today()
    serving = (QueueToken.query
               .filter(QueueToken.branch_id == branch.id,
                       QueueToken.status.in_(ACTIVE_STATUSES),
                       QueueToken.queue_date == today)
               .order_by(QueueToken.called_at.desc(), QueueToken.id.desc())
               .all())
    waiting_query = QueueToken.query.filter_by(
        branch_id=branch.id, status=TokenStatus.WAITING, queue_date=today)
    waiting_next = (waiting_query
                    .order_by(QueueToken.is_priority.desc(), QueueToken.id.asc())
                    .limit(5).all())

    return {
        "branch_id":     branch.id,
        "branch":        branch.name,
        "waiting_count": waiting_query.count(),
        "serving": [{
            "token":        t.token_number,
            "counter":      t.counter.label if t.counter else "",
            "service":      t.service_type.name,
            "service_code": t.service_type.code,
            "is_priority":  t.is_priority,
        } for t in serving],
        "next": [{
            "token":        t.token_number,
            "service":      t.service_type.name,
            "service_code": t.service_type.code,
            "is_priority":  t.is_priority,
        } for t in waiting_next],
    }


def serving_payload(token) -> dict:
    """What the staff console needs to show the patient being served."""
    return {
        "token_id":        token.id,
        "token_number":    token.token_number,
        "customer_name":   token.display_name,
        "service":         token.service_type.name,
        "status":          token.status.value,
        "is_priority":     token.is_priority,
        "priority_reason": token.priority_reason.value,
        "counter":         token.counter.label if token.counter else None,
        "counter_id":      token.counter_id,
    }


def push_queue_update(branch_id: int):
    """Emit live queue state to all SocketIO clients watching this branch."""
    from app.extensions import db
    from app.models import Branch
    try:
        branch = db.session.get(Branch, branch_id)
        if branch is None:
            return
        payload = build_display_state(branch)
        socketio.emit("queue_update", payload, room=f"branch_{branch_id}")
        logger.debug("Pushed queue update for branch %s: %s waiting",
                     branch_id, payload["waiting_count"])
    except Exception:
        logger.exception("push_queue_update failed")
