# HiBob Sync — Provisioning & Offboarding Flow Timeline

**Date:** 2026-07-01
**Status:** Approved (design)
**Author:** Omri Cohen (with Claude)

## Problem

When the KAPPIT agent runs the new-employee provisioning or employee-offboarding
PowerShell script, the full run log is already captured per request in
`ProvisioningRequest.result_log` / `OffboardingRequest.result_log`. But on the
HiBob Sync dashboard (`/hibob-sync/`) the only way to see what happened is a
"View log" link that opens the raw text in a new tab. If a step fails or warns,
there is no at-a-glance indication on the employee's line — you have to open the
raw log and read it.

**Goal:** Surface the relevant flow information inline on each new-employee /
terminated-employee line, so that if there is an error or issue it is visible on
that line without opening the raw log.

## Non-Goals

- No changes to the KAPPIT PowerShell scripts or the agent (Kdesk-only feature).
- No new data capture — we parse the `result_log` that is already stored.
- No database migration.
- Not building offboarding itself (already exists); only its dashboard display.

## Approach

Parse the stored `result_log` on the Kdesk side and map it onto a **fixed,
ordered stage list** per flow. Render as an **inline expandable timeline**
(chosen UX, "Option A"): each row shows a status badge and a chevron; expanding
reveals the step-by-step timeline with failed/warning steps highlighted and the
exact log line shown inline.

### Why Kdesk-only free-text parsing (vs. structured markers in the scripts)

Every log line carries a stable `[INFO]` / `[WARN]` / `[ERROR]` level tag emitted
by the scripts' `Write-Log` function (not per-message wording). This lets us
surface **all** warnings/errors reliably regardless of how any individual message
is phrased. The per-stage labels are matched from log wording (best-effort); if a
message is later reworded, at worst a stage label drifts — the underlying
error/warning still appears in the issues summary. This gives immediate value
across all existing and future logs with zero KAPPIT deploy friction. (Adding
machine-readable `[STEP]` markers to the scripts was considered and deferred as a
possible future hardening if free-text matching proves brittle in practice.)

## Components

### 1. `hibob_sync/flow_parser.py` (new — pure, testable)

The core unit. Pure functions, no Django/DB access, never raises.

```
parse_provisioning_flow(log_text) -> FlowResult
parse_offboarding_flow(log_text)  -> FlowResult
```

`FlowResult`:
- `stages`: ordered list of `Stage(key, label, status, detail_lines)`
- `overall`: `ok | warning | failed | unknown`
- `issues`: list of every `[WARN]` / `[ERROR]` line found (the robustness net)

`Stage.status` ∈ `done | warning | failed | skipped | not_reached`.

**Detection:** each stage has one or more stable substring/regex matchers against
the log. A stage with no evidence and that comes *after* the last-seen stage is
`not_reached`; one explicitly skipped ("… skipped") is `skipped`; one with an
associated `[WARN]`/`[ERROR]` line is `warning`/`failed`.

**Level extraction:** independently of stage matching, collect all lines tagged
`[WARN]`/`[ERROR]` into `issues`, and attach each to the nearest preceding stage
where possible. This guarantees no error is ever hidden.

**Empty / missing log:** returns empty `stages`, `overall = unknown`.

#### Provisioning stage list (ordered)
1. Request received & validated
2. Existing-account check
3. Manager resolved
4. AD account created
5. Added to AD security group
6. AD Connect (KADSYNC) delta triggered
7. Synced to M365
8. M365 groups assigned (X of N)
9. Manager credentials email sent
10. System tickets (Priority / Salesforce) — **sourced from the request's ticket
    flags/state, not the PS log** (ticket creation happens Kdesk-side)
11. Completed & reported

#### Offboarding stage list (ordered)
1. Employee found in AD
2. Manager resolved
3. AD account disabled + manager cleared
4. Removed from AD groups
5. Moved to deletion OU
6. AD Connect (KADSYNC) delta triggered
7. Exchange Online connect
8. Mailbox converted to shared
9. Manager granted mailbox access
10. Removed from M365 / AAD groups
11. Manager granted OneDrive access
12. Completed & reported

### 2. View integration (`hibob_sync/views.py` dashboard view)

- For the recent provisioning and offboarding requests already loaded (~20 each),
  parse `result_log` and attach a `flow` (`FlowResult`) to each object.
- Compute a cheap per-row `flow_status` badge value: `ok` (green) / `warning`
  (amber) / `failed` (red), derived from `flow.overall` (falling back to
  `result_success` when there is no log yet).
- Parsing ~20 short logs per render is inexpensive; no caching or migration.

### 3. Template (`templates/hibob_sync/dashboard.html`)

- Each provisioning/offboarding row: name + **status badge** (green / amber / red)
  + chevron toggle.
- Expanded panel (Bootstrap collapse) renders the timeline: one line per stage
  with its status icon (✅ / ⚠️ / ✖️ / ➖ / ❔), label, and any attached
  `[WARN]`/`[ERROR]` log line styled inline (amber for warn, red for error).
- The existing "View raw log" link moves/stays **inside** the expanded panel
  (reuses `hibob_sync_provisioning_log`; add the offboarding-log route if it does
  not already exist).

### 4. Live-polling coexistence

The provisioning/offboarding tabs already poll every ~3s and re-render rows. The
poll must **not** collapse a panel the user has expanded. Chosen handling: the
poll updates only the status/badge cells and leaves expanded panels intact (exact
mechanism finalized in the implementation plan). A row whose status changes gets
its badge updated in place.

## Error Handling

- `flow_parser` never raises; on any unexpected content it still returns the
  `issues` list and marks unmatched stages `not_reached`/`unknown`.
- Empty `result_log` → badge falls back to `result_success`; expanded panel shows
  "No log available yet."

## Testing

`flow_parser` is pure → unit tests drive it with real sample logs captured from
actual runs:
- clean success (all `done`)
- completed-with-warnings (e.g. M365 group partial failure → amber)
- failed-early / aborted mid-flow (later stages `not_reached`)
- disabled-user-found and active-user-found review paths
- manager-not-found warning
- empty log

Assertions cover: correct `overall`, correct per-stage statuses, and that every
`[WARN]`/`[ERROR]` line appears in `issues`.

## Rollout

Pure Kdesk change: deploy via the standard sequence (Docker build → ACR → restart
the 3 app services). No KAPPIT changes, no migration. Works retroactively on all
existing `result_log` values.
