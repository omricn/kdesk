"""Parse a KAPPIT provisioning/offboarding PowerShell run log into a structured,
ordered flow timeline for display on the HiBob Sync dashboard.

Pure module - no Django/DB imports, never raises. See
docs/superpowers/specs/2026-07-01-hibob-flow-timeline-design.md.

Log line format (emitted by the scripts' Write-Log function):
    [YYYY-MM-DD HH:MM:SS] [LEVEL] message
where LEVEL is INFO / WARN / ERROR.

Each flow maps onto a FIXED, ORDERED stage list. A stage's status is derived
from the log lines that mention it: an ERROR line -> failed, a WARN line ->
warning, otherwise (any matching INFO line) -> done. Stages with no matching
line are 'skipped' if an earlier-and-later stage did log, else 'not_reached'
(the run stopped before getting there).

Independently of stage matching, EVERY [WARN]/[ERROR] line is collected into
`issues` - so an error is never hidden even if a message is reworded and its
stage label drifts. `overall` is driven by issues as well as stage statuses.
"""
import re
from dataclasses import dataclass, field

_LINE_RE = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(INFO|WARN|ERROR)\] (.*)$'
)

# Each entry: (key, label, [substring patterns identifying lines for this stage])
_PROVISIONING_STAGES = [
    ('received',       'Request received & validated',    ['Provisioning request #', 'Employee:']),
    ('existing_check', 'Existing-account check',          ['Searching AD for existing account', 'existing account',
                                                           'Disabled AD account found', 'Active AD account already exists']),
    ('manager',        'Manager resolved',                ['Resolving manager', 'Manager DN', 'Manager not found',
                                                           'skipping manager', "Manager '"]),
    ('ad_account',     'AD account created',              ['AD user created', 'AD user creation FAILED', 'Would create AD user']),
    ('ad_group',       'Added to AD security group',      ['Adding to AD group', 'AD group']),
    ('kadsync',        'AD Connect (KADSYNC) delta',      ['AD Connect delta', 'KADSYNC', 'schtasks']),
    ('m365_sync',      'Synced to M365',                  ['Polling for M365 user', 'M365 user found',
                                                           'M365 user not found', 'not yet in M365']),
    ('m365_groups',    'M365 groups assigned',            ['Adding user to', 'Added to:', 'M365 group',
                                                           'could not be assigned', 'via Exchange Online']),
    ('creds_email',    'Manager credentials email',       ['Credentials stored', 'Manager notification email',
                                                           'storing credentials', 'credentials not sent',
                                                           'skipping credentials notification', 'Failed to store credentials']),
    ('completed',      'Completed & reported',            ['Provisioning complete', 'Report submitted']),
]

_OFFBOARDING_STAGES = [
    ('found',          'Employee found in AD',            ['Searching AD for employee', 'Found AD account',
                                                           'Employee not found in AD']),
    ('manager',        'Manager resolved',                ['Searching AD for manager', 'Found manager',
                                                           "Manager '", 'mailbox delegation']),
    ('disabled',       'AD account disabled + cleared',   ['Disabled AD account', 'Cleared manager attribute']),
    ('ad_groups',      'Removed from AD groups',          ['Removed from AD group', 'removing all except',
                                                           'Failed to remove from AD group']),
    ('moved_ou',       'Moved to deletion OU',            ['deletion OU']),
    ('kadsync',        'AD Connect (KADSYNC) delta',      ['AD Connect delta', 'KADSYNC']),
    ('exo_connect',    'Exchange Online connect',         ['Connecting to Exchange Online', 'Exchange Online connected',
                                                           'Failed to connect to Exchange Online']),
    ('mailbox_shared', 'Mailbox converted to shared',     ['Converted mailbox to shared', 'Failed to convert mailbox']),
    ('mailbox_access', 'Manager granted mailbox access',  ['Granted mailbox FullAccess', 'Failed to grant mailbox access']),
    ('m365_groups',    'Removed from M365 / AAD groups',  ['Removed from M365 group', 'Removed from EXO group',
                                                           'AAD group membership', 'Failed to remove from M365 group',
                                                           'Failed to remove from EXO group', 'Failed to enumerate']),
    ('onedrive',       'Manager granted OneDrive access', ['OneDrive site', 'Granted OneDrive', 'OneDrive delegation',
                                                           'site admin']),
    ('completed',      'Completed & reported',            ['Offboarding completed', 'Offboarding complete', 'Report submitted']),
]


@dataclass
class Issue:
    level: str      # 'WARN' | 'ERROR'
    message: str


@dataclass
class Stage:
    key: str
    label: str
    status: str = 'not_reached'   # done | warning | failed | skipped | not_reached
    detail_lines: list = field(default_factory=list)   # WARN/ERROR messages for this stage


@dataclass
class FlowResult:
    stages: list = field(default_factory=list)
    overall: str = 'unknown'      # ok | warning | failed | unknown
    issues: list = field(default_factory=list)

    def stage(self, key):
        for s in self.stages:
            if s.key == key:
                return s
        return None


def _parse_lines(log_text):
    """Return list of (level, message) for each recognized log line."""
    out = []
    for raw in (log_text or '').splitlines():
        m = _LINE_RE.match(raw)
        if m:
            out.append((m.group(2), m.group(3)))
    return out


def _build(stage_specs, log_text):
    parsed = _parse_lines(log_text)
    issues = [Issue(level, msg) for level, msg in parsed if level in ('WARN', 'ERROR')]

    stages = []
    last_matched = -1
    for idx, (key, label, patterns) in enumerate(stage_specs):
        matched = [(lvl, msg) for lvl, msg in parsed if any(p in msg for p in patterns)]
        stage = Stage(key=key, label=label)
        if matched:
            last_matched = idx
            levels = {lvl for lvl, _ in matched}
            if 'ERROR' in levels:
                stage.status = 'failed'
            elif 'WARN' in levels:
                stage.status = 'warning'
            else:
                stage.status = 'done'
            stage.detail_lines = [msg for lvl, msg in matched if lvl in ('WARN', 'ERROR')]
        else:
            stage.status = None   # resolved positionally below
        stages.append(stage)

    # Positional resolution for stages with no direct evidence.
    for idx, stage in enumerate(stages):
        if stage.status is None:
            stage.status = 'skipped' if idx < last_matched else 'not_reached'

    overall = _overall(stages, issues, any_matched=last_matched >= 0)
    return FlowResult(stages=stages, overall=overall, issues=issues)


def _overall(stages, issues, any_matched):
    if not any_matched:
        return 'unknown'
    has_error = any(s.status == 'failed' for s in stages) or any(i.level == 'ERROR' for i in issues)
    has_warn = any(s.status == 'warning' for s in stages) or any(i.level == 'WARN' for i in issues)
    if has_error:
        return 'failed'
    if has_warn:
        return 'warning'
    return 'ok'


def parse_provisioning_flow(log_text):
    return _build(_PROVISIONING_STAGES, log_text)


def parse_offboarding_flow(log_text):
    return _build(_OFFBOARDING_STAGES, log_text)
