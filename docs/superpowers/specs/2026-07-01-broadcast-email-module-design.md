# Broadcast Email Module — Design

**Date:** 2026-07-01
**Author:** Omri Cohen (with Claude)
**Status:** Approved for planning

## Purpose

A superuser-only page in Kdesk to compose and send a fully branded Kramer email
(identical to the automated "card" emails Kdesk already sends) to arbitrary
recipients, with a live preview before sending. Replaces the manual
"open HTML file → copy/paste into Outlook" workflow for one-off announcements
(e.g. the Salesforce login-issue notice).

**v1 scope:** plain-text announcements rendered through the existing branded
template. Out of scope for v1 (possible later): attachments, scheduling,
rich-text editor, per-send audit log in the DB, choosing header color.

## Access & Placement

- Gated by `is_superuser` — the same gate used by Settings, Budget, and HiBob
  Sync. Reuses the existing pattern from `tickets/views.py` `email_preview`
  (`if not request.user.is_superuser: return HttpResponseForbidden()`), with a
  redirect to `portal_dashboard` for the full-page view to match `admin_required`
  behavior.
- New sidebar link in the superuser section of `templates/base.html`
  (`{% if user.is_superuser %}`), icon `bi-megaphone`, label **"Broadcast"**,
  in both the mobile (~line 116) and desktop (~line 183) nav blocks.
- Route in `tickets/urls.py`:
  `path('broadcast/', views.broadcast_email, name='broadcast_email')`.
  Lives alongside the other admin tools — no new Django app.

## The Form (left column)

Fields:

| Field | Input | Notes |
|-------|-------|-------|
| **To** | text, comma-separated | **Optional.** `dir="auto"`. Hint: "Leave empty to send Bcc-only — To defaults to servicedesk@kramerav.com." |
| **Bcc** | text, comma-separated | Optional. `dir="auto"`. |
| **Subject** | text | Required. |
| **Header title** | text | Required. The large purple heading (e.g. "Salesforce Login Issue"). |
| **Sub-line** | text | Optional. Small text under the title in the header. |
| **Body** | textarea, plain text | Required. `dir="auto"`. Blank lines → paragraphs; single newlines → `<br>`. Same transform as the ticket-reply box. |

All inputs use Kdesk form tokens (`.form-control`, `.form-label`, etc.).

### Quick-pick distribution lists

Beside both the To and Bcc fields, render "quick add" chips/buttons that append
an address to that field. Defined once as a Python constant so more can be added
later:

```python
BROADCAST_QUICK_RECIPIENTS = [
    "IL_All_Employees@kramerav.com",
    "Global_All_Employees@kramerav.com",
]
```

Clicking a chip appends the address (comma-separated) to the associated field.
Manual typing is always allowed.

## Live Preview (right column)

- An `<iframe>` rendering the real `_email_html(...)` output — the genuine
  branded email, so WYSIWYG.
- Re-renders as the user edits (debounced), using the same iframe technique the
  existing `email_preview` page already uses. Preview is produced by the same
  view responding to a `?preview=1` request (returns just the rendered email
  HTML, no chrome), so the preview and the sent email come from one code path.

## Sending

1. "Send" button POSTs the form.
2. Server validation:
   - Subject non-empty.
   - Body non-empty.
   - At least one valid recipient across To + Bcc (after the To-default rule below).
   - Basic email-shape validation on each address; invalid addresses reported back.
3. **To-default rule (Bcc-only support):** if To is empty, set
   `to = [SERVICEDESK_EMAIL]` so the message is valid and Bcc recipients stay
   hidden from each other.
4. Render body: escape, then `\n\n` → paragraph breaks and `\n` → `<br>`.
5. Build the email with the existing branded template:
   `_email_html(header_title=<title>, header_subtitle=<sub-line>,
   greeting=<body_html>, body_rows='', cta_url=None)` — no details table,
   no CTA button.
6. Respect the `emails_enabled` SystemSetting kill-switch (same as
   `ticket_send_email`): if off, warn and block.
7. Send via `graph_client.send_email(from_mailbox=SERVICEDESK_EMAIL,
   to_email=<list>, bcc_email=<list>, subject=<subject>, body_html=<html>)`.
8. On success: green alert "Email sent to N recipient(s)."; redirect back to the
   form (Post/Redirect/Get). The servicedesk mailbox **Sent Items** folder is the
   audit trail — no new DB model.

### Loop safety (why the To-default is safe)

Sending from `servicedesk@kramerav.com` delivers a self-copy to the servicedesk
inbox. This does **not** create a ticket or a notification loop: `poll_mailbox`
in `integrations/email_poller.py` (lines ~121-137) hard-skips any inbound email
whose **sender** is the servicedesk mailbox — it moves the message to Deleted
Items and records an EmailLog entry `self-email skipped`, before any
ticket-creation logic runs. The skip keys off the sender, so it applies
regardless of whether servicedesk appears in To, Cc, or Bcc. This is the same
guard the existing Change-broadcast feature relies on for its self-copies, so
the Broadcast module reuses a proven path.

### Shared change: `graph_client.send_email` accepts lists

`send_email` currently takes single `to_email` / `bcc_email` strings. Extend both
to accept **either a string or a list of strings**, building the appropriate
`toRecipients` / `bccRecipients` arrays. Backward-compatible — all existing
callers pass strings and keep working.

## Defaults

- **From mailbox:** `settings.SERVICEDESK_EMAIL` (`servicedesk@kramerav.com`) —
  the sender for every Kdesk email.
- **Header color:** fixed Kramer purple `#8205B4` (template default).

## Components / Files Touched

- `integrations/graph_client.py` — `send_email` accepts str-or-list for To/Bcc.
- `tickets/views.py` — new `broadcast_email` view (GET form, `?preview=1`
  preview render, POST send) + `BROADCAST_QUICK_RECIPIENTS` constant.
- `tickets/urls.py` — new `broadcast/` route.
- `templates/tickets/broadcast.html` — the two-column compose + preview page.
- `templates/base.html` — sidebar "Broadcast" link (mobile + desktop blocks).

## Error Handling

- Emails disabled → warning message, no send.
- No recipients after To-default → error, no send (shouldn't happen since To
  defaults, but guarded).
- Invalid email address(es) → error listing the bad entries, no send.
- Graph send exception → error message with the failure surfaced; form values
  preserved so the user can retry.

## Testing

- Body transform: blank-line paragraphs and single-newline `<br>` render correctly;
  HTML in the body is escaped (no injection).
- To-default rule: empty To → To becomes `[servicedesk@kramerav.com]`.
- `send_email` list handling: string and list inputs both build correct
  recipient arrays (unit test with a mocked `post`).
- Access: non-superuser is blocked.
- Preview path and send path produce identical email HTML for the same inputs.
