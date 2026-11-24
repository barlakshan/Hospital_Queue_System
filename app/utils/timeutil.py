"""Time-zone helpers.

Datetimes are stored in UTC (naive in the database). The queue "day" and
every time shown to staff use the hospital's local zone (APP_TIMEZONE,
default Asia/Colombo), independent of the server's own time zone.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Colombo"


def local_tz() -> ZoneInfo:
    try:
        from flask import current_app
        name = current_app.config.get("APP_TIMEZONE") or DEFAULT_TZ
    except RuntimeError:  # outside an app context
        name = DEFAULT_TZ
    return ZoneInfo(name)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return datetime.now(local_tz())


def local_today():
    return local_now().date()


def to_local(dt):
    """Convert a stored (naive = UTC) or aware datetime to local time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz())


def iso_local(dt):
    """ISO-8601 string with the UTC offset, for JSON APIs."""
    local = to_local(dt)
    return local.isoformat() if local else None


def as_utc(dt):
    """Normalise a stored datetime to aware UTC for arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def minutes_between(start, end):
    if not start or not end:
        return None
    return int((as_utc(end) - as_utc(start)).total_seconds() // 60)


def local_day_start_utc(day=None):
    """Naive-UTC datetime of local midnight, for comparing with stored values."""
    day = day or local_today()
    start = datetime(day.year, day.month, day.day, tzinfo=local_tz())
    return start.astimezone(timezone.utc).replace(tzinfo=None)
