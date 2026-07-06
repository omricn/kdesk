# Litigation-Hold Handling in the Termination Flow

**Date:** 2026-07-06
**Status:** Approved (design)
**Author:** Omri Cohen (with Claude)

## Problem

When a departing employee's Exchange Online mailbox has **litigation hold** enabled,
`Set-Mailbox -Type Shared` fails. The termination flow cannot convert the mailbox to a
shared mailbox, and therefore cannot grant the manager access to it.

Today this failure is silently absorbed: in `Offboard-Employee.ps1` the conversion is
wrapped in a `try/catch` that logs a `WARN` and continues. The rest of the flow (AD
account disable, group stripping, 365 group removal) proceeds, leaving a broken end
state — the account is offboarded but the manager has no shared-mailbox access, and the
failure is buried in the log rather than surfaced for action.

## Goal

For every termination flow, detect litigation hold before performing the
hold-blocked 365 operations. If a hold is present, disable it, wait for the removal to
take effect, and only then convert the mailbox to shared and remove 365 groups. If the
hold cannot be cleared in a bounded time, leave the 365 side untouched and surface the
request for a human retry.

## Decisions (locked)

1. **Hold policy — auto-disable, always.** When litigation hold is detected, the flow
   disables it automatically and unattended. No approval gate. (Trade-off accepted: a
   litigation hold usually exists for a legal/compliance reason; auto-removal has no
   built-in legal safeguard. Owner chose full automation for operational simplicity.)
2. **Wait strategy — in-script poll + retry, bounded budget.** The same PowerShell run
   disables the hold, then polls until it clears, up to a bounded budget. If it does not
   clear in budget, the request is reported for review; a human retries later once the
   change has propagated.
3. **Gating — AD first, gate only 365.** AD operations (disable account, clear manager,
   strip AD groups, move to deletion OU, KADSYNC delta) run first, unchanged. Only the
   hold-blocked 365 operations are gated on the hold clearing.
4. **Wait budget — 15 minutes** (config-driven default).
5. **OneDrive delegation runs regardless.** Granting the manager OneDrive site-collection
   admin (Graph step 7c) is not hold-related, so it runs even on the hold-not-cleared path.

## Scope

**In scope:** Exchange Online **litigation hold** (`LitigationHoldEnabled`) only.

**Out of scope:** Other hold types that can also block mailbox operations — In-Place Hold,
retention-policy holds (`ComplianceTagHoldApplied`), delay holds (`DelayHoldApplied`),
and `InPlaceHolds` entries. These are not handled by this change. If they surface as a
blocker in practice, they are a follow-up.

## Design

### Location

- **`C:\Scripts\HiBob_To_AD\Offboard-Employee.ps1`** — new logic inside the existing
  `if ($exoConnected)` block, as a step **6a-pre** that runs before the current
  `Set-Mailbox -Type Shared` (6a). AD Steps 4–5 are unchanged.
- **`hibob_sync/views.py` → `api_offboarding_report`** — one new status mapping.
- **`config.json`** (on KAPPIT, not in repo) — one new optional field.

### Flow

AD Steps 2–5 (find employee/manager, disable account, clear manager, strip AD groups,
move to deletion OU, trigger KADSYNC) run first, exactly as today.

Then, after connecting to Exchange Online:

**Step 6a-pre — litigation hold check**

1. `Get-Mailbox -Identity $EmployeeEmail` → read `LitigationHoldEnabled`.
2. **If `LitigationHoldEnabled` is `$false`** → log "no litigation hold, proceeding" and
   fall through to 6a. No behavior change for the common case.
3. **If `LitigationHoldEnabled` is `$true`:**
   - Log a prominent `WARN`: `LITIGATION HOLD ENABLED on <email> — disabling for offboarding`.
   - `Set-Mailbox -Identity $EmployeeEmail -LitigationHoldEnabled $false`.
   - **Poll loop** (bounded by `$holdWaitMinutes`, default 15; interval 30s):
     - Sleep 30s.
     - Re-`Get-Mailbox` and read `LitigationHoldEnabled`.
     - If `$false` → set `$holdCleared = $true`, log the elapsed time, break.
   - If the budget elapses while still `$true` → `$holdCleared = $false`.

**Gating on `$holdCleared`:**

- **Cleared (or hold was never on):** proceed exactly as today —
  - 6a convert to shared (`Set-Mailbox -Type Shared`)
  - 6b delegate mailbox FullAccess to manager
  - 6c remove from EXO distribution / mail-enabled groups
  - 7b remove from M365 / cloud security groups (Graph)
  - 7c grant manager OneDrive site admin
  - Report success (unchanged path).

- **Not cleared within budget:**
  - **Skip** the hold-blocked 365 work: 6a conversion, 6b mailbox delegation,
    6c EXO groups, 7b M365 groups.
  - **Still run** 7c OneDrive delegation (not hold-related; helps the manager regardless,
    and is idempotent on a later retry).
  - Report with message prefixed `LITIGATION_HOLD_UNCLEARED:` and a human-readable
    detail (employee email + elapsed wait). AD work stays done; the mailbox and its 365
    groups are left fully intact for retry.

### DryRun behavior

Under `-DryRun`:
- Log whether litigation hold is detected.
- If detected, log `[DRY RUN] would disable litigation hold` and
  `[DRY RUN] would wait up to N min for hold to clear` — **do not** call `Set-Mailbox`
  and **do not** sleep. Treat the hold as cleared so the rest of the dry-run flow logs
  its normal 365 steps.

### Config

New optional field in `config.json` on KAPPIT:

```json
"LitigationHoldWaitMinutes": 15
```

- Read defensively: if absent or non-numeric, default to `15`.
- Poll interval is fixed at 30 seconds (not configurable).

**Concurrency note:** During the wait, the single-threaded `agent.py` poll loop is blocked
from picking up other provisioning/offboarding requests. This is acceptable given low
offboarding volume, and the 15-minute budget sits well under the agent's ~45-minute task
ceiling.

### Server-side status mapping

In `hibob_sync/views.py → api_offboarding_report`, add handling for the new prefix,
mirroring the existing `EMPLOYEE_NOT_FOUND:` pattern:

- If `result_message` starts with `LITIGATION_HOLD_UNCLEARED:` → `new_status = 'review_needed'`.
- Send a notification (and/or ticket comment) so the team knows a manual retry is needed
  once the hold-removal propagates. Reuse the existing offboarding notification path with
  an appropriate outcome (e.g. a `review`/`hold` variant of the not-found notification).
- `success`/`failed`/`not_found` paths are otherwise unchanged.

### Retry path

There is no dedicated offboarding re-run endpoint. The human retry path for a
`review_needed` (hold-uncleared) request is **`offboarding_manual_trigger`**
(`hibob_sync/views.py`), which queues an offboarding by employee email. On the re-run:

- AD operations are effectively idempotent (disabling an already-disabled account,
  removing already-removed groups, moving an already-moved object all no-op or warn
  harmlessly).
- The litigation hold is now off (disabled on the first run and since propagated), so the
  conversion and 365 group removal succeed.

## Testing & verification

The PowerShell script cannot run in the local dev environment (no Exchange Online / Graph
connectivity; Django cannot run locally). Verification is therefore:

1. **`-DryRun` manual run** on KAPPIT to confirm control flow and logging for both the
   hold-present and no-hold branches.
2. **Real run against a test mailbox with litigation hold enabled** before relying on the
   change in production, to confirm the disable → poll → convert sequence and the
   uncleared-timeout path.
3. Server-side status mapping (`LITIGATION_HOLD_UNCLEARED:` → `review_needed`) is
   unit-testable in `hibob_sync` tests against `api_offboarding_report`.

This will not be claimed "verified" on the basis of local syntax checks alone.

## Out of scope / future

- Handling non-litigation hold types (In-Place, retention, delay holds).
- Re-enabling the litigation hold after conversion (not required — the account is being
  offboarded and the mailbox becomes shared).
- A dedicated offboarding retry endpoint (manual trigger suffices for now).
