# Litigation-Hold Handling in the Termination Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect Exchange Online litigation hold during offboarding, auto-disable it, wait (bounded) for it to clear, then convert the mailbox to shared and remove 365 groups — and if the hold cannot be cleared in budget, leave the 365 side untouched and surface the request for a human retry.

**Architecture:** Two touch-points. (1) `Offboard-Employee.ps1` on KAPPIT gets a new pre-step inside the Exchange block that checks/disables/polls litigation hold and gates the hold-blocked 365 operations on a `$holdCleared` flag. (2) `hibob_sync/views.py::api_offboarding_report` maps a new `LITIGATION_HOLD_UNCLEARED:` message prefix to `review_needed` (mirroring the existing `EMPLOYEE_NOT_FOUND:` handling) and sends a notification. AD operations are unchanged and still run first.

**Tech Stack:** PowerShell 5.1 (ASCII-only script per KAPPIT constraints), Django 4/5, Exchange Online PowerShell (`Set-Mailbox`, `Get-Mailbox`), SQLite-backed `settings_test` for local Django tests.

**Design spec:** `docs/superpowers/specs/2026-07-06-litigation-hold-offboarding-design.md`

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `hibob_sync/views.py` | Modify `api_offboarding_report` (~line 1002) + `_send_offboarding_notification` (~1173) + `_post_offboarding_ticket_comment` (~1146) | Map `LITIGATION_HOLD_UNCLEARED:` → `review_needed`; notify team |
| `hibob_sync/test_offboarding_report.py` | Create | Unit tests for the report-endpoint status mapping |
| `C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1` | Modify | Litigation-hold check/disable/poll + gating of 365 ops |
| `config.json` (on KAPPIT, not in repo) | Add `LitigationHoldWaitMinutes` | Wait-budget knob (default 15) |

**Ordering:** Do Task 1 first (server-side, fully TDD-able locally). Then Tasks 2–4 (PowerShell — not locally runnable; verified by parse-check locally and a real dry-run/test-mailbox run on KAPPIT).

---

## Task 1: Server-side — map `LITIGATION_HOLD_UNCLEARED:` to `review_needed`

**Files:**
- Test: `hibob_sync/test_offboarding_report.py` (create)
- Modify: `hibob_sync/views.py` — `api_offboarding_report` (~1002), `_post_offboarding_ticket_comment` (~1146), `_send_offboarding_notification` (~1173)

- [ ] **Step 1: Write the failing test**

Create `hibob_sync/test_offboarding_report.py`:

```python
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from hibob_sync.models import OffboardingRequest

API_KEY = 'test-sync-key'
REPORT_URL = '/hibob-sync/api/offboarding/report/'


@override_settings(HIBOB_SYNC_API_KEY=API_KEY)
class OffboardingReportStatusTests(TestCase):
    def _claimed_req(self, **kw):
        base = dict(
            employee_email='jdoe@kramerav.com',
            employee_name='John Doe',
            direct_manager='Jane Boss',
            country_origin='Israel',
            status='claimed',
        )
        base.update(kw)
        return OffboardingRequest.objects.create(**base)

    def _post(self, body):
        return self.client.post(
            REPORT_URL,
            data=json.dumps(body),
            content_type='application/json',
            **{'HTTP_X_SYNC_API_KEY': API_KEY},
        )

    @patch('hibob_sync.views._post_offboarding_ticket_comment')
    @patch('hibob_sync.views._send_offboarding_notification')
    def test_litigation_hold_uncleared_maps_to_review_needed(self, mock_notify, mock_comment):
        req = self._claimed_req()
        resp = self._post({
            'req_id': req.id,
            'success': False,
            'message': 'LITIGATION_HOLD_UNCLEARED: jdoe@kramerav.com — hold not cleared within 15 min',
            'log': 'irrelevant',
        })
        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'review_needed')
        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args.kwargs.get('outcome')
                         or mock_notify.call_args.args[1], 'hold_review')

    @patch('hibob_sync.views._post_offboarding_ticket_comment')
    @patch('hibob_sync.views._send_offboarding_notification')
    def test_plain_failure_still_maps_to_failed(self, mock_notify, mock_comment):
        req = self._claimed_req()
        resp = self._post({
            'req_id': req.id,
            'success': False,
            'message': 'Unexpected error: boom',
        })
        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'failed')

    @patch('hibob_sync.views._send_manager_onedrive_notification')
    @patch('hibob_sync.views._create_offboarding_system_tickets')
    @patch('hibob_sync.views._post_offboarding_ticket_comment')
    @patch('hibob_sync.views._send_offboarding_notification')
    def test_success_still_maps_to_completed(self, mock_notify, mock_comment,
                                             mock_tickets, mock_od):
        req = self._claimed_req()
        with patch('tasks.scheduled.run_sentinel_verification'):
            resp = self._post({'req_id': req.id, 'success': True, 'message': 'done'})
        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'completed')
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python manage.py test hibob_sync.test_offboarding_report --settings=kdesk.settings_test -v 2`
Expected: `test_litigation_hold_uncleared_maps_to_review_needed` FAILS — status is `failed`, not `review_needed`, and `outcome` is `failed`, not `hold_review`. The other two tests PASS.

- [ ] **Step 3: Add the prefix mapping in `api_offboarding_report`**

In `hibob_sync/views.py`, in `api_offboarding_report`, find the existing block (~line 1021):

```python
    EMPLOYEE_NOT_FOUND_PREFIX = 'EMPLOYEE_NOT_FOUND:'
    is_not_found = isinstance(result_message, str) and result_message.startswith(EMPLOYEE_NOT_FOUND_PREFIX)

    if is_not_found:
        new_status = 'review_needed'
    else:
        new_status = 'completed' if success else 'failed'
```

Replace it with:

```python
    EMPLOYEE_NOT_FOUND_PREFIX = 'EMPLOYEE_NOT_FOUND:'
    LITIGATION_HOLD_PREFIX = 'LITIGATION_HOLD_UNCLEARED:'
    is_not_found = isinstance(result_message, str) and result_message.startswith(EMPLOYEE_NOT_FOUND_PREFIX)
    is_hold_uncleared = isinstance(result_message, str) and result_message.startswith(LITIGATION_HOLD_PREFIX)

    if is_not_found or is_hold_uncleared:
        new_status = 'review_needed'
    else:
        new_status = 'completed' if success else 'failed'
```

- [ ] **Step 4: Route the post-report actions for the hold case**

In the same function, find the post-report action block (~line 1039):

```python
        if is_not_found:
            _post_offboarding_ticket_comment(req, outcome='not_found')
            _send_offboarding_notification(req, outcome='not_found')
        elif success:
```

Insert a new branch **before** the `elif success:` branch (after the `if is_not_found:` branch):

```python
        if is_not_found:
            _post_offboarding_ticket_comment(req, outcome='not_found')
            _send_offboarding_notification(req, outcome='not_found')
        elif is_hold_uncleared:
            _post_offboarding_ticket_comment(req, outcome='hold_review')
            _send_offboarding_notification(req, outcome='hold_review')
        elif success:
```

(Leave the rest of the `elif success:` / `else:` branches unchanged. The hold case, like `not_found`, does NOT trigger sentinel verification.)

- [ ] **Step 5: Add the `hold_review` copy to the ticket comment helper**

In `_post_offboarding_ticket_comment` (~line 1146), add an `elif` before the final `else:` (which handles the generic failure):

```python
        elif outcome == 'not_found':
            body = (
                f'Offboarding could not proceed — employee account not found in AD.\n'
                f'Searched by email: {req.employee_email}\n'
                f'Please verify the account manually and handle offboarding steps if needed.\n'
            )
        elif outcome == 'hold_review':
            body = (
                f'Offboarding partially completed — LITIGATION HOLD could not be cleared in time.\n'
                f'Account: {req.employee_email}\n'
                f'AD account disabled and moved to deletion OU. The mailbox was NOT converted to '
                f'shared and 365 groups were NOT removed, because the litigation hold had not '
                f'finished clearing.\n'
                f'Action: once the hold-removal has propagated, re-run offboarding for this '
                f'employee from the Kdesk offboarding dashboard.\n'
            )
        else:
```

- [ ] **Step 6: Add the `hold_review` branch to the notification helper**

In `_send_offboarding_notification` (~line 1186), add an `elif` before the final `else:` (generic failure) branch, alongside the existing `not_found` branch:

```python
        elif outcome == 'not_found':
            subject      = f'Offboarding Blocked — Employee Not Found in AD ({req.employee_email})'
            header_color = '#fd7e14'
            header_title = 'Offboarding Blocked — Employee Not Found'
            greeting     = (f'Hi Kdesk Team,<br><br>The AD account for <strong>{display_name}</strong> '
                            f'was not found by email address. Manual action may be required.')
        elif outcome == 'hold_review':
            subject      = f'Offboarding Needs Review — Litigation Hold ({req.employee_email})'
            header_color = '#fd7e14'
            header_title = 'Offboarding Paused — Litigation Hold Not Cleared'
            greeting     = (f'Hi Kdesk Team,<br><br>The offboarding of <strong>{display_name}</strong> '
                            f'completed the AD steps, but the mailbox litigation hold did not clear in '
                            f'time, so the shared-mailbox conversion and 365 group removal were skipped. '
                            f'Once the hold-removal has propagated, re-run offboarding for this employee '
                            f'from the dashboard.')
        else:
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python manage.py test hibob_sync.test_offboarding_report --settings=kdesk.settings_test -v 2`
Expected: all three tests PASS.

- [ ] **Step 8: Run the broader hibob_sync suite to confirm no regressions**

Run: `python manage.py test hibob_sync --settings=kdesk.settings_test -v 1`
Expected: OK (no failures).

- [ ] **Step 9: Commit**

```bash
git add hibob_sync/test_offboarding_report.py hibob_sync/views.py
git commit -m "feat(offboarding): map LITIGATION_HOLD_UNCLEARED report to review_needed"
```

---

## Task 2: PowerShell — config knob + `Wait-LitigationHoldCleared` helper

> **Note:** `Offboard-Employee.ps1` cannot be executed in the local dev environment (no Exchange Online / no Django). These PowerShell tasks are verified locally by a **parse-check** and on KAPPIT by a real dry-run. TDD red-green does not apply; correctness of code shown is required.

**Files:**
- Modify: `C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1`

- [ ] **Step 1: Read the config knob defensively**

In `Offboard-Employee.ps1`, immediately after the config load (currently line 50, `$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json`), add:

```powershell
# Litigation-hold wait budget (minutes). Optional in config.json; default 15.
$holdWaitMinutes = 15
if ($config.PSObject.Properties.Name -contains 'LitigationHoldWaitMinutes') {
    $parsedWait = 0
    if ([int]::TryParse([string]$config.LitigationHoldWaitMinutes, [ref]$parsedWait) -and $parsedWait -gt 0) {
        $holdWaitMinutes = $parsedWait
    }
}
```

- [ ] **Step 2: Add the `Wait-LitigationHoldCleared` helper**

Add this function in the helpers region, after the `Invoke-GraphApi` function definition (currently ends ~line 171) and before the `# Main body` comment (~line 173):

```powershell
# ---------------------------------------------------------------------------
# Litigation hold helper
# ---------------------------------------------------------------------------

function Wait-LitigationHoldCleared {
    <#
        Disables litigation hold on the mailbox, then polls until the change
        takes effect or the timeout elapses. Returns $true if the hold is
        (or becomes) cleared, $false if it does not clear within the budget.
        Under -DryRun it disables nothing, does not sleep, and returns $true.
    #>
    param(
        [Parameter(Mandatory=$true)][string]$Identity,
        [int]$TimeoutMinutes = 15,
        [switch]$DryRun
    )

    if ($DryRun) {
        Write-Log "[DRY RUN] would disable litigation hold and wait up to $TimeoutMinutes min for it to clear"
        return $true
    }

    Write-Log "Disabling litigation hold on $Identity" 'WARN'
    Set-Mailbox -Identity $Identity -LitigationHoldEnabled $false

    $intervalSec = 30
    $start       = Get-Date
    $deadline    = $start.AddMinutes($TimeoutMinutes)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $intervalSec
        $mbx = Get-Mailbox -Identity $Identity -ErrorAction SilentlyContinue
        if ($mbx -and -not $mbx.LitigationHoldEnabled) {
            $elapsedSec = [int]((Get-Date) - $start).TotalSeconds
            Write-Log "Litigation hold cleared after ~$elapsedSec s"
            return $true
        }
        Write-Log "Litigation hold still enabled - waiting..."
    }

    Write-Log "Litigation hold did NOT clear within $TimeoutMinutes min" 'ERROR'
    return $false
}
```

Note: keep the file **ASCII-only** (per KAPPIT constraint) — the ellipses above are three ASCII dots, not a Unicode character.

- [ ] **Step 3: Parse-check the script locally**

Run:
```bash
powershell -NoProfile -Command "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1',[ref]$null,[ref]$e); if($e){$e|ForEach-Object{$_.Message}}else{'PARSE OK'}"
```
Expected: `PARSE OK` (no parser errors).

- [ ] **Step 4: Commit**

```bash
git add "C:/Scripts/HiBob_To_AD/Offboard-Employee.ps1"
git commit -m "feat(offboard): add litigation-hold wait helper and config knob"
```

---

## Task 3: PowerShell — gate the 365 operations on the hold clearing

**Files:**
- Modify: `C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1`

- [ ] **Step 1: Initialize the gate flags before the Exchange block**

The Exchange block currently begins (~line 261) with:

```powershell
    Write-Log "Connecting to Exchange Online..."
    $exoConnected = $false
```

Change it to also initialize the hold flags (so they are always defined for the final report under `Set-StrictMode -Version Latest`, even if EXO never connects):

```powershell
    Write-Log "Connecting to Exchange Online..."
    $exoConnected  = $false
    $holdCleared   = $true    # true unless a hold is found and does NOT clear
    $holdUncleared = $false   # true only when a hold was found and did not clear in budget
```

- [ ] **Step 2: Insert the litigation-hold pre-step and gate 6a/6b/6c**

The current `if ($exoConnected) { ... }` block (~lines 276–324) starts with `# 6a. Convert mailbox to shared`. Replace the entire body of that `if ($exoConnected)` block with the version below. The 6a/6b/6c logic is unchanged except that it is now wrapped in `if ($holdCleared) { ... }`, preceded by the hold check:

```powershell
    if ($exoConnected) {
        # 6a-pre. Litigation hold check — must clear before converting to shared
        try {
            $mbx = Get-Mailbox -Identity $EmployeeEmail -ErrorAction Stop
            if ($mbx.LitigationHoldEnabled) {
                Write-Log "LITIGATION HOLD ENABLED on $EmployeeEmail - handling before shared conversion" 'WARN'
                $holdCleared = Wait-LitigationHoldCleared -Identity $EmployeeEmail -TimeoutMinutes $holdWaitMinutes -DryRun:$DryRun
                if (-not $holdCleared) { $holdUncleared = $true }
            } else {
                Write-Log "No litigation hold on $EmployeeEmail - proceeding with conversion"
            }
        } catch {
            Write-Log "Failed to read litigation hold status: $_ - proceeding with conversion attempt" 'WARN'
        }

        if ($holdCleared) {
            # 6a. Convert mailbox to shared
            try {
                if (-not $DryRun) { Set-Mailbox -Identity $EmployeeEmail -Type Shared }
                Write-Log "$(if ($DryRun) {'[DRY RUN] '})Converted mailbox to shared"
            } catch {
                Write-Log "Failed to convert mailbox to shared: $_" 'WARN'
            }

            # 6b. Grant manager full access to mailbox (only if manager was found)
            if ($managerUPN) {
                try {
                    if (-not $DryRun) {
                        Add-MailboxPermission -Identity $EmployeeEmail -User $managerUPN `
                            -AccessRights FullAccess -InheritanceType All -AutoMapping $true | Out-Null
                    }
                    Write-Log "$(if ($DryRun) {'[DRY RUN] '})Granted mailbox FullAccess to: $managerUPN"
                } catch {
                    Write-Log "Failed to grant mailbox access to manager: $_" 'WARN'
                }
            }

            # 6c. Remove from all EXO distribution/mail-enabled groups
            try {
                $exoGroups = Get-Recipient -ResultSize Unlimited `
                    -Filter "Members -eq '$($employeeAD.DistinguishedName)'" `
                    -RecipientTypeDetails MailUniversalDistributionGroup,MailUniversalSecurityGroup `
                    -ErrorAction SilentlyContinue
                if ($exoGroups) {
                    foreach ($grp in $exoGroups) {
                        try {
                            if (-not $DryRun) {
                                Remove-DistributionGroupMember -Identity $grp.Identity `
                                    -Member $EmployeeEmail -Confirm:$false
                            }
                            Write-Log "$(if ($DryRun) {'[DRY RUN] '})Removed from EXO group: $($grp.DisplayName)"
                        } catch {
                            Write-Log "Failed to remove from EXO group $($grp.DisplayName): $_" 'WARN'
                        }
                    }
                } else {
                    Write-Log "No EXO distribution/mail-enabled groups found for this user."
                }
            } catch {
                Write-Log "Failed to enumerate EXO groups: $_" 'WARN'
            }
        } else {
            Write-Log "Skipping shared conversion, mailbox delegation, and EXO group removal - litigation hold not cleared" 'WARN'
        }

        Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue
    }
```

- [ ] **Step 3: Gate the Graph M365 group removal (7b) on `$holdCleared`**

The current Graph group-removal block (~line 356) begins:

```powershell
    # 7b. Remove from all M365/cloud security groups
    if ($employeeAADId) {
```

Change the guard to also require `$holdCleared`, and add an else-log:

```powershell
    # 7b. Remove from all M365/cloud security groups
    if ($employeeAADId -and $holdCleared) {
```

Then, at the end of that `if` block (after its closing `}`, before the `# 7c` comment ~line 380), add:

```powershell
    elseif ($employeeAADId -and -not $holdCleared) {
        Write-Log "Skipping M365 group removal - litigation hold not cleared" 'WARN'
    }
```

Leave **7c (OneDrive delegation) unchanged** — it must run regardless of `$holdCleared`, per the design.

- [ ] **Step 4: Make the Graph-token-failure early exit respect the hold state**

The Graph token acquisition catch block (~line 333) currently does an unconditional success report and exit:

```powershell
    } catch {
        Write-Log "Graph token acquisition failed: $_ - Graph steps will be skipped" 'WARN'
        # Skip Graph steps - report success for completed AD/EXO work
        Write-Log "Offboarding completed (Graph steps skipped)."
        Send-Report -Success $true -Message "Offboarding completed for $EmployeeEmail (Graph steps skipped due to token failure)"
        exit 0
    }
```

Replace its report/exit lines so an uncleared hold is still surfaced:

```powershell
    } catch {
        Write-Log "Graph token acquisition failed: $_ - Graph steps will be skipped" 'WARN'
        if ($holdUncleared) {
            Write-Log "Offboarding needs review (litigation hold not cleared; Graph steps also skipped)."
            Send-Report -Success $false -Message "LITIGATION_HOLD_UNCLEARED: $EmployeeEmail - hold not cleared within $holdWaitMinutes min; AD done, 365 mailbox/groups left for retry (Graph token also failed)"
        } else {
            Write-Log "Offboarding completed (Graph steps skipped)."
            Send-Report -Success $true -Message "Offboarding completed for $EmployeeEmail (Graph steps skipped due to token failure)"
        }
        exit 0
    }
```

- [ ] **Step 5: Make the final success report respect the hold state**

The end of the main `try` block (~line 420) currently reads:

```powershell
    Write-Log "Offboarding completed."
    Send-Report -Success $true -Message "Offboarding completed for $EmployeeEmail"
    exit 0
```

Replace with:

```powershell
    if ($holdUncleared) {
        Write-Log "Offboarding needs review - litigation hold not cleared; 365 mailbox conversion and group removal were skipped."
        Send-Report -Success $false -Message "LITIGATION_HOLD_UNCLEARED: $EmployeeEmail - hold not cleared within $holdWaitMinutes min; AD offboarding done, 365 mailbox/groups left for retry"
    } else {
        Write-Log "Offboarding completed."
        Send-Report -Success $true -Message "Offboarding completed for $EmployeeEmail"
    }
    exit 0
```

- [ ] **Step 6: Parse-check the script locally**

Run:
```bash
powershell -NoProfile -Command "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1',[ref]$null,[ref]$e); if($e){$e|ForEach-Object{$_.Message}}else{'PARSE OK'}"
```
Expected: `PARSE OK`.

- [ ] **Step 7: ASCII-only check (KAPPIT constraint)**

Run:
```bash
powershell -NoProfile -Command "$b=[IO.File]::ReadAllBytes('C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1'); ($b | Where-Object {$_ -gt 127}).Count"
```
Expected: `0` (no non-ASCII bytes). If non-zero, find and replace the offending characters with ASCII equivalents.

- [ ] **Step 8: Commit**

```bash
git add "C:/Scripts/HiBob_To_AD/Offboard-Employee.ps1"
git commit -m "feat(offboard): gate 365 mailbox conversion and group removal on litigation-hold clearing"
```

---

## Task 4: KAPPIT runtime verification (manual, on-server)

> This task is executed on the KAPPIT server (172.16.0.54), not locally. It is the real behavioral verification the design calls for. Do not mark the feature "verified" without it.

- [ ] **Step 1: Add the config knob on KAPPIT (optional)**

Edit `C:\Scripts\HiBob_To_AD\config.json` and add (optional — omit to use the default 15):

```json
"LitigationHoldWaitMinutes": 15
```

- [ ] **Step 2: Dry-run against a mailbox WITHOUT a hold**

Run on KAPPIT:
```powershell
powershell -File C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1 -ReqId dryrun-nohold -EmployeeEmail <test-user-no-hold@kramerav.com> -ManagerName "<Manager Display Name>" -DryRun
```
Expected in the log: `No litigation hold ... proceeding`, then `[DRY RUN] Converted mailbox to shared`, and the normal `[DRY RUN]` group-removal lines. No sleeping.

- [ ] **Step 3: Dry-run against a mailbox WITH litigation hold enabled**

Enable litigation hold on a disposable test mailbox first (`Set-Mailbox <test> -LitigationHoldEnabled $true`), then:
```powershell
powershell -File C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1 -ReqId dryrun-hold -EmployeeEmail <test-user-with-hold@kramerav.com> -ManagerName "<Manager Display Name>" -DryRun
```
Expected: `LITIGATION HOLD ENABLED ...`, then `[DRY RUN] would disable litigation hold and wait up to 15 min ...`, and — because dry-run treats the hold as cleared — the `[DRY RUN]` conversion + group-removal lines. No real `Set-Mailbox` call, no sleeping.

- [ ] **Step 4: Real run against the test mailbox with litigation hold enabled**

With litigation hold still enabled on the disposable test mailbox, run **without** `-DryRun` (use a real `ReqId` for a test OffboardingRequest, or accept that the report POST will 409 if no matching claimed request exists — the local log is the source of truth here):
```powershell
powershell -File C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1 -ReqId <real-or-test-req> -EmployeeEmail <test-user-with-hold@kramerav.com> -ManagerName "<Manager Display Name>"
```
Verify in the log (`C:\Scripts\HiBob_To_AD\logs\Offboard_*.log`):
- `Disabling litigation hold on <email>`
- Either `Litigation hold cleared after ~N s` followed by `Converted mailbox to shared`, **or** (if it exceeds budget) `Litigation hold did NOT clear within 15 min` followed by `Skipping shared conversion ...` and `Skipping M365 group removal ...`.
- Confirm the manager OneDrive delegation line appears in **both** outcomes (7c runs regardless).

- [ ] **Step 5: Confirm the Kdesk status on the uncleared path (if reproduced)**

If Step 4 hit the uncleared timeout with a real claimed request, confirm the OffboardingRequest shows **`review_needed`** in the Kdesk offboarding dashboard and that the review notification email was received.

- [ ] **Step 6: Clean up the test mailbox**

Revert the disposable test mailbox: re-enable litigation hold if it should stay held, convert back from shared if converted, and remove any test group changes.

---

## Self-Review

**Spec coverage:**
- Auto-disable always → Task 3 Step 2 (no approval gate). ✓
- In-script poll + retry, bounded budget → Task 2 Step 2 (`Wait-LitigationHoldCleared`). ✓
- AD first, gate only 365 → Task 3 Steps 2–3 (AD block untouched; 6a/6b/6c and 7b gated). ✓
- 15-min default budget, config-driven → Task 2 Step 1 + Task 4 Step 1. ✓
- OneDrive (7c) runs regardless → Task 3 Step 3 (explicitly left unchanged). ✓
- Not-cleared → `review_needed` + notify → Task 1 (server) + Task 3 Steps 4–5 (report message). ✓
- DryRun behavior → Task 2 Step 2 (helper) + Task 3 (uses `-DryRun:$DryRun`). ✓
- Out-of-scope hold types → not implemented, matching spec. ✓
- Retry path via manual trigger → covered by review_needed status; no new endpoint (matches spec). ✓

**Placeholder scan:** No TBD/TODO; all code shown in full; test bodies concrete; `<test-user@...>`/`<Manager Display Name>` are runtime inputs for the manual KAPPIT task, not code placeholders. ✓

**Type/name consistency:** `$holdCleared`, `$holdUncleared`, `$holdWaitMinutes`, `Wait-LitigationHoldCleared`, prefix `LITIGATION_HOLD_UNCLEARED:`, and outcome `hold_review` are used consistently across Tasks 1–4. ✓
