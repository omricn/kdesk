"""
Business-hours SLA utilities for Kdesk.

Default config (can be overridden via Settings page → SystemSetting):
  sla_work_start : 8       (08:00)
  sla_work_end   : 17      (17:00)
  sla_hours      : 9       (9 business hours)
  sla_work_days  : 6,0,1,2,3  (Sun–Thu, Python weekday: Mon=0 … Sun=6)

All public functions accept and return timezone-aware UTC datetimes.
The conversion to local time for the "is this inside work hours?" check
is done internally using Django's TIME_ZONE setting (Asia/Jerusalem).
"""
from datetime import timedelta
from django.utils import timezone

# Module-level defaults — used as fallback if DB read fails
_WORK_START_DEFAULT = 8
_WORK_END_DEFAULT   = 17
_SLA_HOURS_DEFAULT  = 9.0
_WORK_DAYS_DEFAULT  = frozenset([6, 0, 1, 2, 3])  # Sun–Thu


def _get_sla_config():
    """Read SLA config from SystemSetting, falling back to compile-time defaults."""
    try:
        from tickets.models import SystemSetting
        work_start = int(SystemSetting.get('sla_work_start', str(_WORK_START_DEFAULT)))
        work_end   = int(SystemSetting.get('sla_work_end',   str(_WORK_END_DEFAULT)))
        sla_hours  = float(SystemSetting.get('sla_hours',    str(_SLA_HOURS_DEFAULT)))
        days_str   = SystemSetting.get('sla_work_days', '6,0,1,2,3')
        work_days  = frozenset(int(d) for d in days_str.split(',') if d.strip().isdigit())
        if not work_days:
            work_days = _WORK_DAYS_DEFAULT
    except Exception:
        work_start = _WORK_START_DEFAULT
        work_end   = _WORK_END_DEFAULT
        sla_hours  = _SLA_HOURS_DEFAULT
        work_days  = _WORK_DAYS_DEFAULT
    return work_start, work_end, sla_hours, work_days


def get_sla_hours() -> float:
    """Return the current SLA target in business hours."""
    _, _, sla_hours, _ = _get_sla_config()
    return sla_hours


# ── Internal helpers ──────────────────────────────────────────────────────────

def _local(dt):
    """Convert a UTC-aware datetime to Asia/Jerusalem local time."""
    return timezone.localtime(dt)


def _day_start(dt_local, work_start):
    return dt_local.replace(hour=work_start, minute=0, second=0, microsecond=0)


def _day_end(dt_local, work_end):
    return dt_local.replace(hour=work_end, minute=0, second=0, microsecond=0)


def _is_work_moment(dt_local, work_days, work_start, work_end):
    return (
        dt_local.weekday() in work_days
        and work_start <= dt_local.hour < work_end
    )


def _advance_to_work_start(dt_local, work_days, work_start, work_end):
    if _is_work_moment(dt_local, work_days, work_start, work_end):
        return dt_local

    if dt_local.weekday() in work_days and dt_local.hour < work_start:
        return _day_start(dt_local, work_start)

    # After hours or weekend — find next work day
    candidate = _day_start(dt_local, work_start) + timedelta(days=1)
    while candidate.weekday() not in work_days:
        candidate += timedelta(days=1)
    return candidate


# ── Public API ────────────────────────────────────────────────────────────────

def add_business_hours(start_dt, hours):
    """
    Given start_dt (UTC-aware), add `hours` business hours and return the
    resulting deadline (UTC-aware), using the current SLA config from Settings.
    """
    work_start, work_end, _, work_days = _get_sla_config()
    remaining = float(hours) * 3600.0
    current   = _advance_to_work_start(_local(start_dt), work_days, work_start, work_end)

    while remaining > 0:
        day_end   = _day_end(current, work_end)
        available = (day_end - current).total_seconds()

        if available >= remaining:
            result = current + timedelta(seconds=remaining)
            return result.astimezone(timezone.utc)

        remaining -= available

        next_day = _day_start(current, work_start) + timedelta(days=1)
        while next_day.weekday() not in work_days:
            next_day += timedelta(days=1)
        current = next_day

    return current.astimezone(timezone.utc)


def business_hours_elapsed(start_dt, end_dt):
    """
    Return the number of business hours elapsed between two UTC-aware datetimes.
    Returns 0.0 if end_dt <= start_dt or no work time has passed.
    """
    if not start_dt or not end_dt or end_dt <= start_dt:
        return 0.0

    work_start, work_end, _, work_days = _get_sla_config()
    current = _advance_to_work_start(_local(start_dt), work_days, work_start, work_end)
    target  = _local(end_dt)

    if current >= target:
        return 0.0

    elapsed = 0.0
    while current < target:
        day_end     = _day_end(current, work_end)
        segment_end = min(day_end, target)
        elapsed    += (segment_end - current).total_seconds()

        if segment_end >= target:
            break

        next_day = _day_start(current, work_start) + timedelta(days=1)
        while next_day.weekday() not in work_days:
            next_day += timedelta(days=1)
        current = next_day

    return elapsed / 3600.0


def sla_deadline_for(created_at):
    """Return the SLA deadline (UTC-aware) for a ticket created at created_at."""
    return add_business_hours(created_at, get_sla_hours())


def get_effective_now():
    """
    Return the effective 'now' for SLA calculations.
    Frozen at the suspension timestamp when SLA is globally paused.
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
