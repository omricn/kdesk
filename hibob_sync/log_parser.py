import re
from datetime import datetime

# [2026-05-17 12:26:36] [INFO] [user@kramerav.com] [DRY RUN] field: 'old' -> 'new' | ...
# [2026-05-17 12:26:36] [INFO] [user@kramerav.com] Updated: field: 'old' -> 'new' | ...
_CHANGE_RE = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[INFO\] \[([^\]@]+@[^\]]+)\] (?:\[DRY RUN\] |Updated: )(.+)$'
)
# field: 'old value' -> 'new value'
_FIELD_RE = re.compile(r"([\w]+): '([^']*)' -> '([^']*)'")

# === HiBob → AD Sync started [DRY RUN] ===
_START_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[INFO\] === HiBob')
# === Sync complete ===
_END_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[INFO\] === Sync complete')
# Any timestamp line (fallback)
_TS_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')

_STAT_LABELS = {
    'matched': re.compile(r'Matched in AD\s*:\s*(\d+)'),
    'updated': re.compile(r'Updated\s*:\s*(\d+)'),
    'skipped': re.compile(r'No changes\s*:\s*(\d+)'),
    'not_found': re.compile(r'Not in AD\s*:\s*(\d+)'),
    'errors': re.compile(r'Errors\s*:\s*(\d+)'),
}


def _parse_dt(s):
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')


def parse_log(log_text):
    """
    Parse a HiBob sync log file and return a dict with:
      started_at, completed_at  (naive datetime or None)
      is_dry_run                (bool)
      matched, updated, skipped, not_found, errors  (int)
      changes  list of {'email', 'field', 'old', 'new'}
    """
    result = {
        'started_at': None,
        'completed_at': None,
        'is_dry_run': False,
        'matched': 0,
        'updated': 0,
        'skipped': 0,
        'not_found': 0,
        'errors': 0,
        'changes': [],
    }

    last_ts = None

    for line in log_text.splitlines():
        # Track the last seen timestamp for fallback
        ts_m = _TS_RE.match(line)
        if ts_m:
            last_ts = _parse_dt(ts_m.group(1))

        # Start line
        m = _START_RE.match(line)
        if m:
            result['started_at'] = _parse_dt(m.group(1))
            result['is_dry_run'] = '[DRY RUN]' in line
            continue

        # End line
        m = _END_RE.match(line)
        if m:
            result['completed_at'] = _parse_dt(m.group(1))
            continue

        # Change line
        m = _CHANGE_RE.match(line)
        if m:
            email = m.group(2)
            for fm in _FIELD_RE.finditer(m.group(3)):
                result['changes'].append({
                    'email': email,
                    'field': fm.group(1),
                    'old': fm.group(2),
                    'new': fm.group(3),
                })
            continue

        # Stats lines
        for key, pattern in _STAT_LABELS.items():
            sm = pattern.search(line)
            if sm:
                result[key] = int(sm.group(1))
                break

    if result['completed_at'] is None and last_ts:
        result['completed_at'] = last_ts

    return result
