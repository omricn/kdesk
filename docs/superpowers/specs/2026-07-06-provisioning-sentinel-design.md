# Provisioning Sentinel — Design

**Status:** Approved (brainstorming) — pending spec review
**Date:** 2026-07-06
**Author:** Omri Cohen + Claude

## 1. Problem & goal

New-hire provisioning and employee-termination runs are executed by the on-prem
KAPPIT agent and only *loosely* observed by Kdesk (a request row + a final
report). A recent incident showed runs can report "done" while the real M365
end-state is incomplete (a missing group), or fail silently at a late step
(report lost to an encoding 500) and sit stuck. On-prem AD account creation was
reliable; **the failures clustered at M365 (sync / groups / license) and
reporting** — all things Kdesk can see and reach directly via Graph.

**Goal:** an oversight layer ("Sentinel") that, for every new-hire and
termination request, independently verifies the intended end-state was actually
achieved (full correctness — M365 + downstream tickets/emails + request
reconciliation), **auto-fixes a known-safe playbook**, and **escalates the rest**
with a rich diagnosis.

## 2. Decisions (from brainstorming)

- **Scope:** full correctness — M365/Entra end-state, downstream Priority/SF
  tickets + notification emails, and Kdesk request reconciliation.
- **Autonomy:** auto-remediate a defined known-safe, idempotent playbook;
  escalate everything else to `Kdesk_Superusers@kramerav.com`.
- **Type:** hybrid — deterministic verifier + playbook for detection/action; an
  LLM (Claude) composes root-cause diagnosis for escalations.
- **Architecture:** Kdesk-centric (Approach 2). Kdesk is the brain (reads M365
  via Graph, owns the DB/downstream, runs the LLM, hosts the dashboard). KAPPIT
  is the "hands" for the few actions only it can do (on-prem AD facts/fixes, and
  Exchange-Online writes for mail-enabled groups).

## 3. Architecture

```
   HiBob email → Kdesk (ProvisioningRequest/OffboardingRequest)
                    │  queues
                    ▼
   KAPPIT agent.py ──runs──> Provision/Offboard PS ──reports──> Kdesk
                                                                  │
                                          report received / periodic sweep
                                                                  ▼
                                                    ┌── Sentinel (Kdesk, Celery) ──┐
                                                    │ Verifier → checks (Graph+DB) │
                                                    │ Remediator → playbook        │
                                                    │ LLM diagnoser (P2, Claude)   │
                                                    └──────────────┬───────────────┘
                                     Kdesk-direct fixes            │  on-prem/EXO fixes
                                  (resend email, create ticket,    │  (add mail-enabled group,
                                   re-queue)                       ▼   AD facts) via AgentJob
                                                            KAPPIT agent.py poll
```

Verification is strictly **post-hoc** — it never blocks or delays the actual
provisioning run.

## 4. Components

- **`Verifier`** (Kdesk, Celery) — runs the check suite for a request, produces a
  `VerificationResult`. Read-only: Graph reads + Kdesk DB.
- **`Remediator`** (Kdesk) — maps failed checks to known-safe fixes; runs
  Kdesk-direct fixes inline, or dispatches an `AgentJob` for on-prem/EXO fixes.
  Caps attempts and re-verifies after acting.
- **`AgentJob` channel** — new Kdesk model + `/hibob-sync/api/agent-jobs/{pending,claim,report}/`
  endpoints, mirroring the existing provisioning claim/report pattern. `agent.py`
  polls it alongside provisioning/offboarding. Carries on-prem AD reads/fixes and
  EXO group writes. (P3.)
- **LLM diagnoser** (Kdesk, P2) — on escalation, sends structured evidence
  (request data, run log, failed checks, remediation attempts) to Claude
  (`claude-sonnet-5` default, `claude-opus-4-8` for hardest) → root-cause +
  suggested fix, included in the escalation email and stored.
- **Dashboard** — per-request verification badge + expandable checklist +
  remediation/audit log. Absorbs the current `sweep_stuck_provisioning` watchdog
  as the "liveness" check.

## 5. Data model

**`VerificationResult`** (linked 1:1-latest to a Provisioning/Offboarding request):
- `request` (FK), `kind` (`provisioning` | `offboarding`)
- `overall`: `ok` | `remediated` | `escalated` | `failed` | `pending`
- `checks`: JSON — list of `{key, label, status: pass|fail|unknown, detail}`
- `remediations`: JSON — list of `{action, target, result, at}`
- `attempts`: int (remediation rounds), `diagnosis`: text (P2)
- `created_at`, `updated_at`

**`AgentJob`** (P3): `kind` (`probe_ad` | `add_m365_group` | `assign_license` | …),
`payload` JSON, `status` (`pending|claimed|done|failed`), `result` JSON, timestamps.

## 6. Check suites

**New hire (completion + correctness):**
1. Request status = `completed`.
2. Entra user exists and is enabled.
3. **All resolved `m365_groups` present** (membership check; mail-enabled groups included).
4. **E5 = membership in `Microsoft 365 E5 Users`.** Licensing is group-based, so
   the group auto-assigns the E5 license — this is covered by check #3, not a
   separate license query/assignment.
5. Mailbox provisioned.
6. Credentials stored + manager credentials email sent.
7. Priority/Salesforce new-user tickets created *if flagged*.
8. On-prem AD account exists + enabled (via KAPPIT `probe_ad`, P3; until then inferred from the run log).

**Termination (completion + correctness):**
Completion signal = **OneDrive-handover email sent (last step) AND the Priority +
Salesforce termination tickets created**.
1. Request status = `completed`.
2. Account disabled; sign-in sessions revoked.
3. Removed from groups / license reclaimed per offboarding policy.
4. Mailbox handled per policy.
5. **OneDrive-handover email sent to manager.**
6. **Priority + Salesforce termination tickets created**, identified by:
   - Subject `TERMINATE USER – Priority – <UserFullName>`, category **IT → Priority → Terminate Employee**.
   - Subject `TERMINATE USER – Salesforce – <UserFullName>`, category **IT → SalesForce → Terminate Employee**.
7. Linked ticket updated.

A check that can't be evaluated (Graph throttling/error) is recorded `unknown`,
never `fail` — so transient errors don't cause false escalations.

## 7. Remediation playbook (known-safe, idempotent)

| Failed check | Action | Where |
|---|---|---|
| Missing M365 group (incl. `Microsoft 365 E5 Users` → auto-grants E5) | add member (Graph; EXO for mail-enabled) | KAPPIT `AgentJob` (P3) |
| Manager creds email not sent | resend | Kdesk-direct |
| Missing Priority/SF ticket | create | Kdesk-direct (`_create_system_tickets`) |
| Stuck / never reported | re-queue / watchdog-fail | Kdesk-direct (existing Retry) |
| AD account absent · novel / ambiguous | **escalate** | LLM + superuser email |

There is **no separate license-assignment action** — adding the `Microsoft 365
E5 Users` group grants the E5 license via group-based licensing.

Each remediation is idempotent and capped (max 2 rounds), then escalate — no fix
loops.

## 8. Data flow & triggers

1. **On report received** — the provisioning/offboarding report handler enqueues
   a `verify` task for the request.
2. **Verify** — run the check suite → build `VerificationResult`.
3. **Remediate** — for failed checks, run playbook actions (Kdesk-direct now,
   `AgentJob` dispatch in P3); re-verify affected checks after.
4. **Escalate** — anything unresolved → (P2) LLM diagnosis → email
   `Kdesk_Superusers@` + flag on dashboard.
5. **Periodic sweep** — re-verify requests that were never verified or are stuck
   (extends `sweep_stuck_provisioning`); also a light drift check on recently
   completed requests.

## 9. Error handling

- Checks are read-only and idempotent; Graph failures → `unknown`, retried, never
  a false alarm.
- Remediations idempotent + attempt-capped; escalate on cap.
- Sentinel is fully decoupled from the run — a Sentinel failure never affects
  provisioning itself.
- All Kdesk↔Graph and Kdesk↔KAPPIT calls use timeouts + retries (per the lessons
  from the incident).

## 10. Phasing

- **P1** — Verifier + `VerificationResult` + dashboard + Kdesk-direct playbook
  (resend email, create ticket, re-queue) + escalation alerts. Requires Graph
  read consent. *Catches the last incident's "completed-but-missing-group" and
  "reported-but-500'd" — detect + escalate even before auto-fix exists.*
- **P2** — LLM (Claude) diagnosis layer for escalations.
- **P3** — `AgentJob` channel: on-prem AD facts + EXO/Graph group & license
  auto-fixes + periodic drift reconciliation.

## 11. Prerequisites & constraints

- **Kdesk Graph app (`4ae6b2c2-1c20-431c-b665-22430fec7e77`) Microsoft Graph
  *Application* permissions (client-credentials, headless) — DONE:** `User.Read.All`
  and `GroupMember.Read.All` already existed; `Directory.Read.All` and
  `Organization.Read.All` added with admin consent. (Add `MailboxSettings.Read`
  only if mailbox state is later checked via Graph.)
- **No license permissions needed** — E5 is granted by `Microsoft 365 E5 Users`
  group membership (group-based licensing), so the Sentinel never touches license
  assignment; verifying/fixing the group is sufficient.
- **All M365 write remediations route through KAPPIT** (its Graph app + EXO cert
  auth handle both regular and mail-enabled groups). Kdesk's app stays read-only.
- **`ANTHROPIC_API_KEY`** in Kdesk env — needed for **P2 only** (LLM diagnosis);
  P1 works without it (templated escalation emails). Models `claude-sonnet-5`
  (default) / `claude-opus-4-8` (hardest).
- New KAPPIT PS helpers must be **ASCII-only** and send **UTF-8 request bodies**
  (Windows PowerShell 5.1 lessons).

## 12. Testing

- Verifier checks & playbook mapping → unit tests with mocked Graph/DB responses
  (pass/fail/unknown paths, idempotency, attempt cap).
- `VerificationResult` model + dashboard rendering.
- P2: LLM prompt construction + response parsing against fixtures (no live calls
  in tests).
- P3 KAPPIT helpers: parse-check under PowerShell 5.1, ASCII-only.

## 13. Observability — how superusers see what's happening

**Terminology:** this is **one orchestrator — "the Sentinel"** — a Celery service
in Kdesk with internal components (Verifier, Remediator, LLM diagnoser), *not* a
swarm of agents. The only other actor is the pre-existing **KAPPIT agent**, which
the Sentinel delegates on-prem/EXO fixes to. So: one new orchestrator + the
existing executor.

Visibility is **both GUI and email**, by design:

- **Kdesk GUI (primary, always-on):**
  - Per-request **verification badge** (✔ ok · ⟳ remediated · ⚠ escalated · ✖ failed)
    on the HiBob Sync dashboard rows, with an expandable **checklist** (each check
    pass/fail/unknown) and a **remediation/audit log** (what was auto-fixed, when,
    outcome).
  - A **Sentinel activity view** — recent verifications across requests, what was
    auto-fixed vs escalated, and any open escalations needing a human.
- **Email (push, low-noise):** superusers are emailed **only on escalation** (with
  the P2 LLM root-cause + suggested fix) — not on every successful/auto-fixed run,
  to avoid alert fatigue. Optional: fold a one-line Sentinel summary into the
  existing weekly digest.

## 14. To confirm during implementation

- Exact objects the offboarding flow emits (the OneDrive email trigger + the two
  termination tickets by subject/category above) to wire the completion check.
- Mailbox-handling policy specifics for the termination "mailbox handled" check.
