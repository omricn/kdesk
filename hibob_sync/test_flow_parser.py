import unittest

from hibob_sync.flow_parser import parse_provisioning_flow, parse_offboarding_flow


def _log(*lines):
    return "\n".join(lines)


class IssueExtractionTests(unittest.TestCase):
    """The robustness net: every WARN/ERROR line is surfaced regardless of wording."""

    def test_collects_warn_and_error_lines_in_order(self):
        log = _log(
            "[2026-07-01 10:00:00] [INFO] === Provisioning request #5 started ===",
            "[2026-07-01 10:00:01] [WARN] Manager 'X' not found in AD  -  skipping manager field.",
            "[2026-07-01 10:00:02] [ERROR] AD user creation FAILED: boom",
        )
        result = parse_provisioning_flow(log)
        self.assertEqual(
            [(i.level, i.message) for i in result.issues],
            [
                ('WARN', "Manager 'X' not found in AD  -  skipping manager field."),
                ('ERROR', 'AD user creation FAILED: boom'),
            ],
        )

    def test_info_lines_are_not_issues(self):
        log = _log("[2026-07-01 10:00:00] [INFO] AD user created: a@b.com")
        self.assertEqual(parse_provisioning_flow(log).issues, [])


def _statuses(result):
    return {s.key: s.status for s in result.stages}


# A realistic clean provisioning run (trimmed to the key markers).
CLEAN_PROVISION = _log(
    "[2026-07-01 10:00:00] [INFO] === Provisioning request #5 started ===",
    "[2026-07-01 10:00:00] [INFO] Employee: Ratna Sinha | IT / Tech | Israel",
    "[2026-07-01 10:00:01] [INFO] Searching AD for existing account for Ratna Sinha...",
    "[2026-07-01 10:00:02] [INFO] Resolving manager: Anand Verma",
    "[2026-07-01 10:00:02] [INFO] Manager DN: CN=Anand,DC=x  UPN: averma@kramerav.com",
    "[2026-07-01 10:00:03] [INFO] AD user created: rsinha@kramerav.com",
    "[2026-07-01 10:00:03] [INFO] Adding to AD group: All Employees",
    "[2026-07-01 10:00:03] [INFO] Added to All Employees",
    "[2026-07-01 10:00:04] [INFO] Triggering AD Connect delta sync on KADSYNC...",
    "[2026-07-01 10:02:00] [INFO] M365 user found (attempt 4): abc-123",
    "[2026-07-01 10:02:01] [INFO] Adding user to 8 M365 groups...",
    "[2026-07-01 10:02:02] [INFO] Added to: Joiners",
    "[2026-07-01 10:02:03] [INFO] Added to: Microsoft 365 E5 Users",
    "[2026-07-01 10:02:10] [INFO] E5 group confirmed: Microsoft 365 E5 Users",
    "[2026-07-01 10:02:10] [INFO] Joiners group confirmed: Joiners",
    "[2026-07-01 10:02:11] [INFO] Credentials stored. Manager notification email will be sent to averma@kramerav.com.",
    "[2026-07-01 10:02:12] [INFO] === Provisioning complete for rsinha@kramerav.com ===",
    "[2026-07-01 10:02:12] [INFO] Report submitted to Kdesk.",
)


class ProvisioningStageTests(unittest.TestCase):
    def test_clean_run_all_key_stages_done_and_overall_ok(self):
        r = parse_provisioning_flow(CLEAN_PROVISION)
        st = _statuses(r)
        for key in ('received', 'existing_check', 'manager', 'ad_account',
                    'ad_group', 'kadsync', 'm365_sync', 'm365_groups',
                    'creds_email', 'completed'):
            self.assertEqual(st.get(key), 'done', f'{key} should be done')
        self.assertEqual(r.overall, 'ok')

    def test_stage_order_is_fixed(self):
        keys = [s.key for s in parse_provisioning_flow(CLEAN_PROVISION).stages]
        self.assertEqual(keys, [
            'received', 'existing_check', 'manager', 'ad_account', 'ad_group',
            'kadsync', 'm365_sync', 'm365_groups', 'creds_email', 'completed',
        ])

    def test_ad_creation_failure_fails_stage_and_marks_later_not_reached(self):
        log = _log(
            "[2026-07-01 10:00:00] [INFO] === Provisioning request #5 started ===",
            "[2026-07-01 10:00:01] [INFO] Searching AD for existing account...",
            "[2026-07-01 10:00:02] [INFO] Manager DN: CN=x  UPN: m@x.com",
            "[2026-07-01 10:00:03] [ERROR] AD user creation FAILED: access denied",
        )
        r = parse_provisioning_flow(log)
        st = _statuses(r)
        self.assertEqual(st['ad_account'], 'failed')
        self.assertEqual(st['m365_sync'], 'not_reached')
        self.assertEqual(st['completed'], 'not_reached')
        self.assertEqual(r.overall, 'failed')

    def test_manager_not_found_is_warning_but_flow_continues(self):
        log = _log(
            "[2026-07-01 10:00:00] [INFO] === Provisioning request #5 started ===",
            "[2026-07-01 10:00:01] [INFO] Searching AD for existing account...",
            "[2026-07-01 10:00:02] [WARN] Manager 'Bob X' not found in AD  -  skipping manager field.",
            "[2026-07-01 10:00:03] [INFO] AD user created: bx@kramerav.com",
            "[2026-07-01 10:00:12] [INFO] === Provisioning complete for bx@kramerav.com ===",
        )
        r = parse_provisioning_flow(log)
        st = _statuses(r)
        self.assertEqual(st['manager'], 'warning')
        self.assertEqual(st['ad_account'], 'done')
        self.assertEqual(r.overall, 'warning')

    def test_m365_group_partial_failure_is_warning(self):
        log = _log(
            "[2026-07-01 10:00:00] [INFO] === Provisioning request #5 started ===",
            "[2026-07-01 10:02:01] [INFO] Adding user to 8 M365 groups...",
            "[2026-07-01 10:02:02] [INFO] Added to: Joiners",
            "[2026-07-01 10:02:05] [WARN] 1 group(s) could not be assigned  -  check log.",
            "[2026-07-01 10:02:12] [INFO] Report submitted to Kdesk.",
        )
        st = _statuses(parse_provisioning_flow(log))
        self.assertEqual(st['m365_groups'], 'warning')


class OffboardingStageTests(unittest.TestCase):
    def test_exo_connect_failure_marks_dependent_stages_skipped(self):
        log = _log(
            "[2026-07-01 10:00:00] [INFO] === Offboarding started for x@kramerav.com ===",
            "[2026-07-01 10:00:01] [INFO] Found AD account: SamAccountName=x",
            "[2026-07-01 10:00:02] [INFO] Found manager: m@kramerav.com",
            "[2026-07-01 10:00:03] [INFO] Disabled AD account: x",
            "[2026-07-01 10:00:04] [INFO] Removed from AD group: CN=g",
            "[2026-07-01 10:00:05] [INFO] Moved to deletion OU: OU=del",
            "[2026-07-01 10:00:06] [WARN] Failed to connect to Exchange Online: boom - EXO steps will be skipped",
            "[2026-07-01 10:00:07] [INFO] Removed from M365 group: SomeGroup",
        )
        r = parse_offboarding_flow(log)
        st = _statuses(r)
        self.assertEqual(st['exo_connect'], 'warning')
        self.assertEqual(st['mailbox_shared'], 'skipped')
        self.assertEqual(st['mailbox_access'], 'skipped')
        self.assertEqual(st['m365_groups'], 'done')
        self.assertEqual(st['onedrive'], 'not_reached')
        self.assertEqual(r.overall, 'warning')


class EdgeCaseTests(unittest.TestCase):
    def test_empty_log_is_unknown(self):
        r = parse_provisioning_flow('')
        self.assertEqual(r.overall, 'unknown')
        self.assertTrue(all(s.status == 'not_reached' for s in r.stages))

    def test_garbage_log_never_raises(self):
        r = parse_provisioning_flow('not a log line\n\x00\xff random')
        self.assertEqual(r.overall, 'unknown')
