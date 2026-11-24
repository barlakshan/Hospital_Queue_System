"""Dashboard metrics, computed from real data.

Every function returns None when there isn't enough data; templates show
"—" for None instead of a made-up number.
"""
from datetime import timedelta

from app.models import QueueToken, TokenStatus, User
from app.utils.timeutil import minutes_between, utcnow

ONLINE_WINDOW = timedelta(minutes=5)
ACTIVE_WINDOW = timedelta(minutes=15)


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values)) if values else None


def pct_change(current, previous):
    """Percent change, or None when there is no baseline."""
    if not previous:
        return None
    return round((current - previous) * 100 / previous)


def avg_service_minutes(tokens):
    return _avg(t.service_duration_minutes for t in tokens
                if t.status == TokenStatus.COMPLETED)


def avg_wait_minutes(branch_id, day):
    """Average minutes from registration to being called, for one day."""
    tokens = QueueToken.query.filter(
        QueueToken.branch_id == branch_id,
        QueueToken.queue_date == day,
        QueueToken.called_at.isnot(None),
    ).all()
    return _avg(minutes_between(t.issued_at, t.called_at) for t in tokens)


def officer_summary(user, day):
    """Served today, change vs yesterday, and average consultation time."""
    def completed_on(d):
        return QueueToken.query.filter_by(
            served_by=user.id, queue_date=d, status=TokenStatus.COMPLETED).all()

    today_tokens = completed_on(day)
    yesterday_count = len(completed_on(day - timedelta(days=1)))
    return {
        "served_today": len(today_tokens),
        "served_delta": len(today_tokens) - yesterday_count,
        "avg_service_min": avg_service_minutes(today_tokens),
    }


def branch_day_comparison(branch_id, day):
    """Today's token count vs the same weekday last week."""
    today = QueueToken.query.filter_by(branch_id=branch_id, queue_date=day).count()
    last_week = QueueToken.query.filter_by(
        branch_id=branch_id, queue_date=day - timedelta(days=7)).count()
    return pct_change(today, last_week)


def officer_avg_minutes(user_id, day):
    tokens = QueueToken.query.filter_by(
        served_by=user_id, queue_date=day, status=TokenStatus.COMPLETED).all()
    return avg_service_minutes(tokens)


def is_recently_active(user, window=ACTIVE_WINDOW):
    from app.utils.timeutil import as_utc
    last = as_utc(user.last_seen)
    return last is not None and utcnow() - last <= window


def online_now():
    cutoff = (utcnow() - ONLINE_WINDOW).replace(tzinfo=None)
    return User.query.filter(User.is_active.is_(True), User.last_seen >= cutoff).count()
