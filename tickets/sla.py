"""
Business-hours SLA utilities for Kdesk.

Work days : Sunday – Thursday  (Israel work week)
Work hours : 08:00 – 17:00 Asia/Jerusalem  (9 h/day)
SLA target : 9 business hours

All public functions accept and return timezone-aware UTC datetimes.
The conversion to local time for the "is this inside work hours?" check
is done internally using Django's TIME_ZONE setting (Asia/Jerusalem).
"""
from datetime import timedelta
from django.utils import timezone

WORK_START = 8    # 08:00
WORK_END   = 17   # 17:00  (exclusive – work ends at the start of this hour)
SLA_HOURS  = 9

# Python datetime.weekday(): Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
WORK_DAYS = frozenset([6, 0, 1, 2, 3])   # Sun, Mon, Tue, Wed, Thu


# ── Internal helpers ──────────────────────────────────────────────────────────

def _local(dt):
    """Convert a UTC-aware datetime to Asia/Jerusalem local time."""
    return timezone.localtime(dt)


def _day_start(dt_local):
    """08:00 on the same calendar day as dt_local."""
    return dt_local.replace(hour=WORK_START, minute=0, second=0, microsecond=0)


def _day_end(dt_local):
    """17:00 on the same calendar day as dt_local."""
    return dt_local.replace(hour=WORK_END, minute=0, second=0, microsecond=0)


def _is_work_moment(dt_local):
    """True if dt_local falls inside a work-day work-hour window."""
    return (
        dt_local.weekday() in WORK_DAYS
        and WORK_START <= dt_local.hour < WORK_END
    )


def _advance_to_work_start(dt_local):
    """
    Return the earliest work moment >= dt_local.

    • If dt_local is already inside work hours → return it unchanged.
    • If dt_local is before 08:00 on a work day → return 08:00 that day.
    • Otherwise (after 17:00, or Friday/Saturday) → return 08:00 on the
      next work day.
    """
    if _is_work_moment(dt_local):
        return dt_local

    if dt_local.weekday() in WORK_DAYS and dt_local.hour < WORK_START:
        return _day_start(dt_local)

    # After hours or weekend — find next work day
    candidate = _day_start(dt_local) + timedelta(days=1)
    while candidate.weekday() not in WORK_DAYS:
        candidate += timedelta(days=1)
    return candidate


# ── Public API ────────────────────────────────────────────────────────────────

def add_business_hours(start_dt, hours):
    """
    Given start_dt (UTC-aware), add `hours` business hours and return the
    resulting deadline (UTC-aware).

    If start_dt falls outside work hours the countdown begins at the next
    work-day start, so the returned deadline is always a work-day work-hour
    timestamp.
    """
    remaining = float(hours) * 3600.0   # seconds left to allocate
    current   = _advance_to_work_start(_local(start_dt))

    while remaining > 0:
        day_end   = _day_end(current)
        available = (day_end - current).total_seconds()

        if available >= remaining:
            result = current + timedelta(seconds=remaining)
            return result.astimezone(timezone.utc)

        remaining -= available

        # Jump to start of next work day
        next_day = _day_start(current) + timedelta(days=1)
        while next_day.weekday() not in WORK_DAYS:
            next_day += timedelta(days=1)
        current = next_day

    # remaining == 0 exactly at day boundary
    return current.astimezone(timezone.utc)


def business_hours_elapsed(start_dt, end_dt):
    """
    Return the number of business hours elapsed between two UTC-aware
    datetimes.  Returns 0.0 if end_dt <= start_dt or if no work time has
    passed (e.g. the ticket was created after hours and end_dt is still
    before the next work start).
    """
    if not start_dt or not end_dt or end_dt <= start_dt:
        return 0.0

    current = _advance_to_work_start(_local(start_dt))
    target  = _local(end_dt)

    if current >= target:
        return 0.0

    elapsed = 0.0
    while current < target:
        day_end      = _day_end(current)
        segment_end  = min(day_end, target)
        elapsed     += (segment_end - current).total_seconds()

        if segment_end >= target:
            break

        # Jump to next work day start
        next_day = _day_start(current) + timedelta(days=1)
        while next_day.weekday() not in WORK_DAYS:
            next_day += timedelta(days=1)
        current = next_day

    return elapsed / 3600.0


def sla_deadline_for(created_at):
    """
    Return the SLA deadline (UTC-aware) for a ticket created at created_at.
    """
    return add_business_hours(created_at, SLA_HOURS)


def get_effective_now():
    """
    Return the effective 'now' for SLA calculations.

    When SLA is globally suspended this returns the moment the suspension
    started, so all progress bars freeze in place rather than advancing.
    """
    from tickets.models import SystemSetting
    if SystemSetting.get('sla_paused', '0') == '1':
        paused_at_str = SystemSetting.get('sla_pause_started_at', '')
        if paused_at_str:
            from django.utils.dateparse import parse_datetime
            dt = parse_datetime(paused_at_str)
            if dt:
                return dt
    return timezone.now()
