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


# A realistic clean offboarding run using the EXACT strings the production
# Offboard-Employee.ps1 emits (sanitized names). This is the regression lock that
# keeps the parser patterns aligned with the script's log output. Note there is no
# "Report submitted" line: the script logs that only AFTER POSTing the report, so the
# COMPLETE banner is the last line present in the stored log body.
CLEAN_OFFBOARD = _log(
    "[2026-07-01 10:00:00] [INFO] ===== Offboard-Employee.ps1 START (ReqId=42 DryRun=False) =====",
    "[2026-07-01 10:00:00] [INFO] Employee: jdoe@kramerav.com  Manager: Jane Boss",
    "[2026-07-01 10:00:00] [INFO] --- Step 2: Looking up employee in AD (jdoe@kramerav.com) ---",
    "[2026-07-01 10:00:01] [INFO] Found AD user: jdoe@kramerav.com (Enabled=True)",
    "[2026-07-01 10:00:01] [INFO] --- Step 3: Looking up manager by DisplayName: Jane Boss ---",
    "[2026-07-01 10:00:01] [INFO] Found manager: jboss@kramerav.com",
    "[2026-07-01 10:00:01] [INFO] --- Step 4: AD operations (DryRun=False) ---",
    "[2026-07-01 10:00:01] [INFO] Disabled AD account: jdoe@kramerav.com",
    "[2026-07-01 10:00:01] [INFO] Cleared Manager attribute.",
    "[2026-07-01 10:00:01] [INFO] Employee has 3 non-primary group membership(s).",
    "[2026-07-01 10:00:01] [INFO] Removed from group: All Employees",
    "[2026-07-01 10:00:02] [INFO] Moved to: OU=Users,OU=Kramer Global For Deletion,OU=Kramer Electronics,DC=kramer,DC=local",
    "[2026-07-01 10:00:02] [INFO] --- Step 5: Exchange Online operations ---",
    "[2026-07-01 10:00:12] [INFO] Connected to Exchange Online.",
    "[2026-07-01 10:00:13] [INFO] No litigation hold on jdoe@kramerav.com - proceeding with conversion",
    "[2026-07-01 10:00:14] [INFO] Converted mailbox to Shared: jdoe@kramerav.com",
    "[2026-07-01 10:00:15] [INFO] Granted FullAccess to: jboss@kramerav.com",
    "[2026-07-01 10:00:15] [INFO] Checking EXO distribution group memberships...",
    "[2026-07-01 10:00:16] [INFO] Found 0 EXO distribution group membership(s).",
    "[2026-07-01 10:00:16] [INFO] Disconnected from Exchange Online.",
    "[2026-07-01 10:00:16] [INFO] --- Step 6: Graph API operations ---",
    "[2026-07-01 10:00:17] [INFO] Graph token acquired.",
    "[2026-07-01 10:00:17] [INFO] AAD user ID: abc-123",
    "[2026-07-01 10:00:18] [INFO] Found 12 AAD group membership(s).",
    "[2026-07-01 10:00:18] [INFO] Removed from AAD group: Some Cloud Group",
    "[2026-07-01 10:00:19] [INFO] Removing 2 mail-enabled security group(s) via EXO...",
    "[2026-07-01 10:00:20] [INFO] Removed from mail-enabled group (EXO): _IL_All_Employees",
    "[2026-07-01 10:00:21] [INFO] OneDrive drive ID: b!abc",
    "[2026-07-01 10:00:21] [INFO] OneDrive personal site URL: https://kramer365-my.sharepoint.com/personal/jdoe_kramerav_com",
    "[2026-07-01 10:00:21] [INFO] --- Step 7: OneDrive site collection admin via SPO ---",
    "[2026-07-01 10:00:22] [INFO] Certificate exported to temp PFX.",
    "[2026-07-01 10:00:23] [INFO] Connected to SPO admin: https://kramer365-admin.sharepoint.com",
    "[2026-07-01 10:00:23] [INFO] Granted Site Collection Admin on OneDrive to jboss@kramerav.com",
    "[2026-07-01 10:00:23] [INFO] ===== Offboard-Employee.ps1 COMPLETE =====",
)


class OffboardingStageTests(unittest.TestCase):
    def test_clean_run_all_stages_done_and_overall_ok(self):
        r = parse_offboarding_flow(CLEAN_OFFBOARD)
        st = _statuses(r)
        for key in ('found', 'manager', 'disabled', 'ad_groups', 'moved_ou',
                    'litigation', 'exo_connect', 'mailbox_shared', 'mailbox_access',
                    'm365_groups', 'onedrive', 'completed'):
            self.assertEqual(st.get(key), 'done', f'{key} should be done')
        self.assertEqual(r.overall, 'ok')

    def test_stage_order_and_no_kadsync(self):
        keys = [s.key for s in parse_offboarding_flow(CLEAN_OFFBOARD).stages]
        self.assertEqual(keys, [
            'found', 'manager', 'disabled', 'ad_groups', 'moved_ou', 'litigation',
            'exo_connect', 'mailbox_shared', 'mailbox_access', 'm365_groups',
            'onedrive', 'completed',
        ])
        self.assertNotIn('kadsync', keys)

    def test_exo_connect_failure_marks_dependent_stages_skipped(self):
        log = _log(
            "[2026-07-01 10:00:00] [INFO] ===== Offboard-Employee.ps1 START (ReqId=7 DryRun=False) =====",
            "[2026-07-01 10:00:00] [INFO] --- Step 2: Looking up employee in AD (x@kramerav.com) ---",
            "[2026-07-01 10:00:01] [INFO] Found AD user: x@kramerav.com (Enabled=True)",
            "[2026-07-01 10:00:01] [INFO] Found manager: m@kramerav.com",
            "[2026-07-01 10:00:02] [INFO] Disabled AD account: x@kramerav.com",
            "[2026-07-01 10:00:02] [INFO] Cleared Manager attribute.",
            "[2026-07-01 10:00:02] [INFO] Employee has 1 non-primary group membership(s).",
            "[2026-07-01 10:00:02] [INFO] Removed from group: All Employees",
            "[2026-07-01 10:00:03] [INFO] Moved to: OU=Users,OU=Kramer Global For Deletion,OU=Kramer Electronics,DC=kramer,DC=local",
            "[2026-07-01 10:00:03] [WARN] Failed to connect to Exchange Online: boom - EXO steps will be skipped.",
            "[2026-07-01 10:00:04] [INFO] Graph token acquired.",
            "[2026-07-01 10:00:04] [INFO] AAD user ID: abc",
            "[2026-07-01 10:00:05] [INFO] Found 4 AAD group membership(s).",
            "[2026-07-01 10:00:05] [INFO] Removed from AAD group: SomeGroup",
            "[2026-07-01 10:00:06] [INFO] --- Step 7: OneDrive site collection admin via SPO ---",
            "[2026-07-01 10:00:07] [INFO] Granted Site Collection Admin on OneDrive to m@kramerav.com",
        )
        st = _statuses(parse_offboarding_flow(log))
        self.assertEqual(st['exo_connect'], 'warning')
        self.assertEqual(st['litigation'], 'skipped')     # EXO down, so hold never checked
        self.assertEqual(st['mailbox_shared'], 'skipped')
        self.assertEqual(st['mailbox_access'], 'skipped')
        self.assertEqual(st['m365_groups'], 'done')
        self.assertEqual(st['onedrive'], 'done')
        self.assertEqual(parse_offboarding_flow(log).overall, 'warning')

    def test_litigation_hold_uncleared_gates_365_but_onedrive_still_runs(self):
        log = _log(
            "[2026-07-01 10:00:00] [INFO] ===== Offboard-Employee.ps1 START (ReqId=8 DryRun=False) =====",
            "[2026-07-01 10:00:00] [INFO] --- Step 2: Looking up employee in AD (x@kramerav.com) ---",
            "[2026-07-01 10:00:01] [INFO] Found AD user: x@kramerav.com (Enabled=True)",
            "[2026-07-01 10:00:01] [INFO] Found manager: m@kramerav.com",
            "[2026-07-01 10:00:02] [INFO] Disabled AD account: x@kramerav.com",
            "[2026-07-01 10:00:02] [INFO] Cleared Manager attribute.",
            "[2026-07-01 10:00:02] [INFO] Employee has 1 non-primary group membership(s).",
            "[2026-07-01 10:00:02] [INFO] Removed from group: All Employees",
            "[2026-07-01 10:00:03] [INFO] Moved to: OU=Users,OU=Kramer Global For Deletion,OU=Kramer Electronics,DC=kramer,DC=local",
            "[2026-07-01 10:00:13] [INFO] Connected to Exchange Online.",
            "[2026-07-01 10:00:14] [WARN] LITIGATION HOLD ENABLED on x@kramerav.com - handling before shared conversion",
            "[2026-07-01 10:00:14] [WARN] Disabling litigation hold on x@kramerav.com",
            "[2026-07-01 10:15:14] [ERROR] Litigation hold did NOT clear within 15 min",
            "[2026-07-01 10:15:14] [WARN] Skipping shared conversion, mailbox delegation, and EXO group removal - litigation hold not cleared",
            "[2026-07-01 10:15:15] [INFO] Graph token acquired.",
            "[2026-07-01 10:15:15] [INFO] AAD user ID: abc",
            "[2026-07-01 10:15:16] [INFO] Found 4 AAD group membership(s).",
            "[2026-07-01 10:15:16] [WARN] Skipping M365 group removal - litigation hold not cleared",
            "[2026-07-01 10:15:17] [INFO] OneDrive personal site URL: https://kramer365-my.sharepoint.com/personal/x_kramerav_com",
            "[2026-07-01 10:15:17] [INFO] --- Step 7: OneDrive site collection admin via SPO ---",
            "[2026-07-01 10:15:18] [INFO] Granted Site Collection Admin on OneDrive to m@kramerav.com",
            "[2026-07-01 10:15:18] [INFO] ===== Offboard-Employee.ps1 COMPLETE =====",
        )
        st = _statuses(parse_offboarding_flow(log))
        self.assertEqual(st['litigation'], 'failed')        # ERROR: hold did not clear
        self.assertEqual(st['mailbox_shared'], 'warning')   # WARN: skipping conversion
        self.assertEqual(st['m365_groups'], 'warning')      # WARN: skipping group removal
        self.assertEqual(st['onedrive'], 'done')            # OneDrive runs regardless
        self.assertEqual(st['completed'], 'done')
        self.assertEqual(parse_offboarding_flow(log).overall, 'failed')


# Real-world shape: the KAPPIT agent reported the log as a PowerShell object, so
# it was stored as an object-repr string with the log under 'value', escaped \r\n
# line breaks, and trailing PS cruft. The parser must still find the entries.
MALFORMED_PROVISION = (
    "{'value': '"
    "[2026-07-01 07:09:18] [INFO] === Provisioning request #15 started  ===\\r\\n"
    "[2026-07-01 07:09:18] [INFO] Employee: Ratna Sinha | Sales Manager - Delhi\\r\\n"
    "[2026-07-01 07:09:18] [INFO] Searching AD for existing account for Ratna Sinha...\\r\\n"
    "[2026-07-01 07:09:18] [INFO] Manager DN: CN=Antriksh Verma (India)\\r\\n"
    "[2026-07-01 07:09:20] [INFO] AD user created: rsinha@kramerav.com\\r\\n"
    "[2026-07-01 07:09:20] [INFO] Added to India Users\\r\\n"
    "[2026-07-01 07:09:20] [INFO] Triggering AD Connect delta sync on KADSYNC...\\r\\n"
    "[2026-07-01 07:10:22] [INFO] M365 user found (attempt 3): 219b7aad\\r\\n"
    "[2026-07-01 07:10:22] [INFO] Adding user to 8 M365 groups...\\r\\n"
    "[2026-07-01 07:10:23] [INFO] Added to: Joiners\\r\\n"
    "[2026-07-01 07:10:49] [INFO] === Provisioning complete for rsinha@kramerav.com ===\\r\\n"
    "', 'PSPath': 'C:\\\\Scripts\\\\HiBob_To_AD\\\\logs\\\\Provision_15.log', 'PSDrive': {'Name': 'C'}}"
)


class MalformedLogTests(unittest.TestCase):
    def test_parses_log_wrapped_in_ps_object_with_escaped_newlines(self):
        r = parse_provisioning_flow(MALFORMED_PROVISION)
        st = _statuses(r)
        self.assertEqual(st['received'], 'done')
        self.assertEqual(st['manager'], 'done')
        self.assertEqual(st['ad_account'], 'done')
        self.assertEqual(st['m365_groups'], 'done')
        self.assertEqual(st['completed'], 'done')
        self.assertEqual(r.overall, 'ok')

    def test_message_detail_has_no_trailing_escaped_newline(self):
        log = (
            "junk {'value': '"
            "[2026-07-01 07:00:00] [WARN] 1 group(s) could not be assigned  -  check log.\\r\\n"
            "', 'PSPath': 'x'}"
        )
        issues = parse_provisioning_flow(log).issues
        self.assertEqual(len(issues), 1)
        self.assertFalse(issues[0].message.endswith('\\r\\n'),
                         'trailing escaped newline should be trimmed')


class EdgeCaseTests(unittest.TestCase):
    def test_empty_log_is_unknown(self):
        r = parse_provisioning_flow('')
        self.assertEqual(r.overall, 'unknown')
        self.assertTrue(all(s.status == 'not_reached' for s in r.stages))

    def test_garbage_log_never_raises(self):
        r = parse_provisioning_flow('not a log line\n\x00\xff random')
        self.assertEqual(r.overall, 'unknown')
