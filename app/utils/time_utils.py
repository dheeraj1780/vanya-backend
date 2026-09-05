"""
Shared naive-UTC datetime helpers.

This codebase writes every timestamp as naive UTC (see
user_repository.soft_delete_user, and every other `datetime.utcnow()`
write), so any comparison against a DB-read value needs to match that —
but a DB-read value's awareness depends on the *dialect*, not on anything
this app controls: SQLite never round-trips tzinfo (a value written as
naive UTC reads back naive), while Postgres's DateTime(timezone=True)
columns (every timestamp column in this schema uses that) round-trip as
timezone-aware UTC. Comparing a fresh datetime.utcnow() against a DB-read
value that's sometimes aware and sometimes not is exactly what raised
"can't compare offset-naive and offset-aware datetimes" — first caught in
auth_service (see git history), then again in billing_service's
create_subscription retry-cooldown check once a real (not just test-mode)
subscription attempt actually exercised that path. Originally fixed
locally inside auth_service as private `_utcnow`/`_naive_utc` helpers;
pulled out here once a second module needed the exact same fix, so it
can't silently go missing a third time.
"""
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Naive UTC 'now' — matches how every timestamp in this codebase is
    written, so it's always safe to compare directly against a DB column
    value that's been passed through naive_utc() below."""
    return datetime.utcnow()


def naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalizes a datetime that may or may not carry tzinfo to naive UTC
    before any arithmetic/comparison against utcnow(). See module
    docstring for why this is required regardless of which DB is behind
    the read."""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)
