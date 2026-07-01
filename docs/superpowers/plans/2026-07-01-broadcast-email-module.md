# Broadcast Email Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a superuser-only "Broadcast" page in Kdesk to compose and send a fully branded Kramer email to arbitrary To/Bcc recipients, with a live preview before sending.

**Architecture:** A single Django view (`broadcast_email` in `tickets/views.py`) renders a two-column page — compose form on the left, live-preview iframe on the right. The preview and the sent email are produced by the same existing branded template helper `_email_html` (in `tasks/scheduled.py`), so they are identical. Pure, dependency-free string/recipient helpers live in a new `tickets/broadcast_utils.py` and are unit-tested with stdlib `unittest`. Sending reuses `graph_client.send_email` (extended to accept a list of recipients) from the `servicedesk@kramerav.com` mailbox — the same mailbox the Change-broadcast feature already uses, so the existing self-email loop guard in the poller covers it.

**Tech Stack:** Django, Bootstrap 5 (Kdesk `custom.css` tokens), Microsoft Graph (`sendMail`), stdlib `unittest`.

---

## File Structure

- **Create** `tickets/broadcast_utils.py` — pure helpers: `BROADCAST_QUICK_RECIPIENTS`, `parse_recipients`, `invalid_emails`, `body_to_html`. No Django imports.
- **Create** `tickets/test_broadcast_utils.py` — stdlib unittest for the above.
- **Create** `integrations/test_graph_recipients.py` — stdlib unittest for the recipient normalizer.
- **Create** `templates/tickets/broadcast.html` — the compose + live-preview page.
- **Modify** `integrations/graph_client.py` — add module-level `_as_recipients()`; make `send_email` accept a string OR a list for `to_email`/`bcc_email`.
- **Modify** `tickets/views.py` — add the `broadcast_email` view (GET form / `?preview=1` render / POST send).
- **Modify** `tickets/urls.py` — add the `broadcast/` route.
- **Modify** `templates/base.html` — add the "Broadcast" sidebar link (mobile + desktop blocks).

---

## Task 1: Pure helpers for recipients and body rendering

**Files:**
- Create: `tickets/broadcast_utils.py`
- Test: `tickets/test_broadcast_utils.py`

- [ ] **Step 1: Write the failing tests**

Create `tickets/test_broadcast_utils.py`:

```python
import unittest

from tickets.broadcast_utils import (
    BROADCAST_QUICK_RECIPIENTS,
    parse_recipients,
    invalid_emails,
    body_to_html,
)


class ParseRecipientsTests(unittest.TestCase):
    def test_empty_returns_empty_list(self):
        self.assertEqual(parse_recipients(''), [])
        self.assertEqual(parse_recipients(None), [])

    def test_splits_on_comma_semicolon_newline_and_trims(self):
        self.assertEqual(
            parse_recipients('a@x.com, b@x.com ;c@x.com\nd@x.com'),
            ['a@x.com', 'b@x.com', 'c@x.com', 'd@x.com'],
        )

    def test_dedupes_case_insensitively_preserving_order(self):
        self.assertEqual(parse_recipients('a@x.com, A@X.com, b@x.com'),
                         ['a@x.com', 'b@x.com'])


class InvalidEmailsTests(unittest.TestCase):
    def test_flags_malformed_addresses(self):
        self.assertEqual(
            invalid_emails(['ok@x.com', 'nope', 'a@b']),
            ['nope', 'a@b'],
        )

    def test_all_valid_returns_empty(self):
        self.assertEqual(invalid_emails(['ok@x.com', 'a.b@sub.x.co']), [])


class BodyToHtmlTests(unittest.TestCase):
    def test_blank_line_separates_paragraphs(self):
        self.assertEqual(
            body_to_html('Hello\n\nWorld'),
            '<p style="margin:0 0 16px;">Hello</p>'
            '<p style="margin:0 0 16px;">World</p>',
        )

    def test_single_newline_becomes_br(self):
        self.assertIn('Line1<br>Line2', body_to_html('Line1\nLine2'))

    def test_escapes_html_to_prevent_injection(self):
        out = body_to_html('<script>alert(1)</script>')
        self.assertNotIn('<script>', out)
        self.assertIn('&lt;script&gt;', out)

    def test_empty_body_returns_empty_string(self):
        self.assertEqual(body_to_html(''), '')
        self.assertEqual(body_to_html(None), '')


class QuickRecipientsTests(unittest.TestCase):
    def test_contains_the_two_all_employee_lists(self):
        self.assertIn('IL_All_Employees@kramerav.com', BROADCAST_QUICK_RECIPIENTS)
        self.assertIn('Global_All_Employees@kramerav.com', BROADCAST_QUICK_RECIPIENTS)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tickets.test_broadcast_utils -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tickets.broadcast_utils'`

- [ ] **Step 3: Write the implementation**

Create `tickets/broadcast_utils.py`:

```python
"""Pure helpers for the Broadcast email module.

Deliberately free of Django imports so they are trivially unit-testable with
stdlib unittest (no DB, no settings, no network).
"""
import html
import re

# Quick-pick distribution lists rendered as chips in the Broadcast form.
# Add more addresses here to expose them in the UI.
BROADCAST_QUICK_RECIPIENTS = [
    'IL_All_Employees@kramerav.com',
    'Global_All_Employees@kramerav.com',
]

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_SPLIT_RE = re.compile(r'[,;\n]+')


def parse_recipients(raw):
    """Split a comma/semicolon/newline-separated string into a de-duplicated,
    order-preserving list of trimmed, non-empty addresses."""
    if not raw:
        return []
    seen = set()
    result = []
    for part in _SPLIT_RE.split(raw):
        addr = part.strip()
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(addr)
    return result


def invalid_emails(addresses):
    """Return the subset of addresses that are not shaped like an email."""
    return [a for a in addresses if not _EMAIL_RE.match(a)]


def body_to_html(text):
    """Convert a plain-text body into safe branded-email HTML.

    Blank lines separate paragraphs; single newlines become <br>. All text is
    HTML-escaped first to prevent injection into the outgoing email.
    """
    if not text:
        return ''
    normalized = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    paragraphs = re.split(r'\n\s*\n', normalized)
    out = []
    for para in paragraphs:
        escaped = html.escape(para).replace('\n', '<br>')
        out.append(f'<p style="margin:0 0 16px;">{escaped}</p>')
    return ''.join(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tickets.test_broadcast_utils -v`
Expected: PASS (all tests OK)

- [ ] **Step 5: Commit**

```bash
git add tickets/broadcast_utils.py tickets/test_broadcast_utils.py
git commit -m "feat: add pure helpers for broadcast email (recipients + body rendering)"
```

---

## Task 2: `send_email` accepts a list of recipients

**Files:**
- Modify: `integrations/graph_client.py` (add `_as_recipients` at module level; use it inside `send_email` at lines ~108-117)
- Test: `integrations/test_graph_recipients.py`

- [ ] **Step 1: Write the failing test**

Create `integrations/test_graph_recipients.py`:

```python
import unittest

from integrations.graph_client import _as_recipients


class AsRecipientsTests(unittest.TestCase):
    def test_string_becomes_single_recipient(self):
        self.assertEqual(
            _as_recipients('a@x.com'),
            [{'emailAddress': {'address': 'a@x.com'}}],
        )

    def test_list_becomes_multiple_recipients(self):
        self.assertEqual(
            _as_recipients(['a@x.com', 'b@x.com']),
            [
                {'emailAddress': {'address': 'a@x.com'}},
                {'emailAddress': {'address': 'b@x.com'}},
            ],
        )

    def test_empty_values_yield_empty_list(self):
        self.assertEqual(_as_recipients(''), [])
        self.assertEqual(_as_recipients(None), [])
        self.assertEqual(_as_recipients([]), [])

    def test_drops_empty_entries_in_list(self):
        self.assertEqual(
            _as_recipients(['a@x.com', '', None]),
            [{'emailAddress': {'address': 'a@x.com'}}],
        )


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest integrations.test_graph_recipients -v`
Expected: FAIL — `ImportError: cannot import name '_as_recipients'`

- [ ] **Step 3: Add the module-level helper**

In `integrations/graph_client.py`, add after the `GRAPH_BASE = ...` line (~line 12), before `class GraphClient:`:

```python
def _as_recipients(value):
    """Normalize a recipient value (a string or a list of strings) into the
    Graph `recipient` dict shape. Empty/falsey entries are dropped."""
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [{'emailAddress': {'address': addr}} for addr in value if addr]
```

- [ ] **Step 4: Use the helper inside `send_email`**

In `integrations/graph_client.py`, replace the recipient-building block in `send_email` (currently lines ~109-117):

```python
        message = {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': body_html},
            'toRecipients': [{'emailAddress': {'address': to_email}}],
        }
        if bcc_email:
            message['bccRecipients'] = [{'emailAddress': {'address': bcc_email}}]
        if cc_emails:
            message['ccRecipients'] = [{'emailAddress': {'address': e}} for e in cc_emails]
```

with:

```python
        message = {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': body_html},
            'toRecipients': _as_recipients(to_email),
        }
        bcc_recipients = _as_recipients(bcc_email)
        if bcc_recipients:
            message['bccRecipients'] = bcc_recipients
        if cc_emails:
            message['ccRecipients'] = [{'emailAddress': {'address': e}} for e in cc_emails]
```

Note: this is backward-compatible — every existing caller passes a single string, and `_as_recipients('x@y.com')` returns the same single-recipient list as before.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest integrations.test_graph_recipients -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add integrations/graph_client.py integrations/test_graph_recipients.py
git commit -m "feat: send_email accepts a list of To/Bcc recipients (backward-compatible)"
```

---

## Task 3: The `broadcast_email` view

**Files:**
- Modify: `tickets/views.py` (add the view immediately after `email_preview`, ~line 1726)

- [ ] **Step 1: Add the view**

In `tickets/views.py`, add this function right after the `email_preview` view ends (after line ~1725, before the `# ── Categories API ──` comment). `SystemSetting`, `settings`, `messages`, `render`, `redirect`, `HttpResponse`, and `HttpResponseForbidden` are already imported at the top of this file.

```python
def broadcast_email(request):
    """Superuser-only tool: compose and send a fully branded Kramer email to
    arbitrary To/Bcc recipients, with a live preview. Sends from the servicedesk
    mailbox — the poller's self-email guard prevents any ticket loop."""
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    from tasks.scheduled import _email_html
    from .broadcast_utils import (
        BROADCAST_QUICK_RECIPIENTS, parse_recipients, invalid_emails, body_to_html,
    )

    # ── Live preview: return ONLY the rendered branded email HTML ──
    # The JS on the page POSTs the current field values here and drops the
    # response into an iframe, so the preview is byte-for-byte the sent email.
    if request.method == 'POST' and request.GET.get('preview') == '1':
        html_out = _email_html(
            header_title=(request.POST.get('header_title', '').strip() or 'Announcement'),
            header_subtitle=request.POST.get('sub_line', '').strip(),
            greeting=(body_to_html(request.POST.get('body', '')) or '&nbsp;'),
            body_rows='',
        )
        resp = HttpResponse(html_out)
        resp['X-Frame-Options'] = 'SAMEORIGIN'
        return resp

    # ── Send ──
    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        header_title = request.POST.get('header_title', '').strip()
        sub_line = request.POST.get('sub_line', '').strip()
        body_text = request.POST.get('body', '').strip()
        to_list = parse_recipients(request.POST.get('to', ''))
        bcc_list = parse_recipients(request.POST.get('bcc', ''))

        form = {
            'subject': subject, 'header_title': header_title, 'sub_line': sub_line,
            'body': body_text, 'to': request.POST.get('to', ''),
            'bcc': request.POST.get('bcc', ''),
        }

        errors = []
        if not subject:
            errors.append('Subject is required.')
        if not header_title:
            errors.append('Header title is required.')
        if not body_text:
            errors.append('Body is required.')
        bad = invalid_emails(to_list + bcc_list)
        if bad:
            errors.append('Invalid email address(es): ' + ', '.join(bad))
        if not to_list and not bcc_list:
            errors.append('Enter at least one To or Bcc recipient.')

        if SystemSetting.get('emails_enabled', '1') != '1':
            errors.append('Email sending is currently disabled. Re-enable it in Settings.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'tickets/broadcast.html', {
                'quick_recipients': BROADCAST_QUICK_RECIPIENTS, 'form': form,
            })

        # Bcc-only support: if To is empty, default it to the sender so the
        # message is valid and Bcc recipients stay hidden from each other. The
        # poller hard-skips emails sent FROM servicedesk, so no ticket loop.
        if not to_list:
            to_list = [settings.SERVICEDESK_EMAIL]

        html_out = _email_html(
            header_title=header_title,
            header_subtitle=sub_line,
            greeting=body_to_html(body_text),
            body_rows='',
        )

        try:
            from integrations.graph_client import get_client
            client = get_client()
            client.send_email(
                from_mailbox=settings.SERVICEDESK_EMAIL,
                to_email=to_list,
                bcc_email=bcc_list or None,
                subject=subject,
                body_html=html_out,
            )
            recipient_count = len(set(a.lower() for a in to_list + bcc_list))
            messages.success(request, f'Email sent to {recipient_count} recipient(s).')
        except Exception as exc:
            messages.error(request, f'Failed to send: {exc}')
            return render(request, 'tickets/broadcast.html', {
                'quick_recipients': BROADCAST_QUICK_RECIPIENTS, 'form': form,
            })
        return redirect('broadcast_email')

    # ── GET → empty form ──
    return render(request, 'tickets/broadcast.html', {
        'quick_recipients': BROADCAST_QUICK_RECIPIENTS, 'form': {},
    })
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 3: Commit**

```bash
git add tickets/views.py
git commit -m "feat: add broadcast_email view (compose, preview, send)"
```

---

## Task 4: URL route

**Files:**
- Modify: `tickets/urls.py` (add after the `dev/email-preview/` line, ~line 29)

- [ ] **Step 1: Add the route**

In `tickets/urls.py`, add this line immediately after the `path('dev/email-preview/', ...)` line:

```python
    path('broadcast/', views.broadcast_email, name='broadcast_email'),
```

- [ ] **Step 2: Verify the URL resolves**

Run: `python manage.py shell -c "from django.urls import reverse; print(reverse('broadcast_email'))"`
Expected: `/broadcast/`

- [ ] **Step 3: Commit**

```bash
git add tickets/urls.py
git commit -m "feat: add /broadcast/ route"
```

---

## Task 5: Sidebar navigation link

**Files:**
- Modify: `templates/base.html` (mobile superuser block ~line 116-121; desktop superuser block ~line 183-188)

- [ ] **Step 1: Add the desktop nav link**

In `templates/base.html`, find the desktop HiBob Sync `<li>` (inside the `{% if user.is_superuser %}` block that uses `data-label`, ~lines 184-188) and insert this `<li>` immediately after its closing `</li>`:

```html
        <li>
          <a href="{% url 'broadcast_email' %}" class="nav-link {% if request.resolver_match.url_name == 'broadcast_email' %}active{% endif %}" data-label="Broadcast">
            <i class="bi bi-megaphone me-2"></i><span class="sidebar-label">Broadcast</span>
          </a>
        </li>
```

- [ ] **Step 2: Add the mobile nav link**

In `templates/base.html`, find the mobile HiBob Sync `<li>` (inside the earlier `{% if user.is_superuser %}` block WITHOUT `data-label`, ~lines 117-121) and insert this `<li>` immediately after its closing `</li>`:

```html
        <li>
          <a href="{% url 'hibob_sync_dashboard' %}" ... </a>  {# marker: this is the existing HiBob link — insert AFTER it #}
        </li>
        <li>
          <a href="{% url 'broadcast_email' %}" class="nav-link {% if request.resolver_match.url_name == 'broadcast_email' %}active{% endif %}">
            <i class="bi bi-megaphone me-2"></i>Broadcast
          </a>
        </li>
```

(Only add the second `<li>` — the first is shown to locate the insertion point.)

- [ ] **Step 3: Verify the template renders**

Run: `python manage.py check`
Expected: no issues. (Visual confirmation happens in Task 7.)

- [ ] **Step 4: Commit**

```bash
git add templates/base.html
git commit -m "feat: add Broadcast link to superuser sidebar (desktop + mobile)"
```

---

## Task 6: The compose + live-preview page

**Files:**
- Create: `templates/tickets/broadcast.html`

- [ ] **Step 1: Create the template**

Create `templates/tickets/broadcast.html`:

```html
{% extends 'base.html' %}
{% block title %}Broadcast Email{% endblock %}

{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
  <nav aria-label="breadcrumb">
    <ol class="breadcrumb mb-0">
      <li class="breadcrumb-item"><a href="{% url 'dashboard' %}">Home</a></li>
      <li class="breadcrumb-item active">Broadcast Email</li>
    </ol>
  </nav>
  <span class="badge bg-primary fs-6"><i class="bi bi-megaphone me-1"></i>Superuser</span>
</div>

<div class="row g-4">
  <!-- Compose form -->
  <div class="col-lg-6">
    <div class="card mb-3">
      <div class="card-header"><i class="bi bi-pencil-square me-1"></i>Compose</div>
      <div class="card-body">
        <form id="broadcastForm" method="post" action="{% url 'broadcast_email' %}">
          {% csrf_token %}

          <div class="mb-3">
            <label class="form-label">To</label>
            <input type="text" name="to" class="form-control" dir="auto"
                   value="{{ form.to|default:'' }}" placeholder="name@kramerav.com, name2@kramerav.com">
            <div class="form-text">
              Comma-separated. Leave empty to send Bcc-only — To defaults to servicedesk@kramerav.com so recipients stay hidden.
            </div>
            <div class="mt-2 d-flex flex-wrap gap-2">
              {% for addr in quick_recipients %}
              <button type="button" class="btn btn-sm btn-outline-secondary quick-chip" data-target="to" data-addr="{{ addr }}">
                <i class="bi bi-plus-lg me-1"></i>{{ addr }}
              </button>
              {% endfor %}
            </div>
          </div>

          <div class="mb-3">
            <label class="form-label">Bcc</label>
            <input type="text" name="bcc" class="form-control" dir="auto"
                   value="{{ form.bcc|default:'' }}" placeholder="Optional">
            <div class="mt-2 d-flex flex-wrap gap-2">
              {% for addr in quick_recipients %}
              <button type="button" class="btn btn-sm btn-outline-secondary quick-chip" data-target="bcc" data-addr="{{ addr }}">
                <i class="bi bi-plus-lg me-1"></i>{{ addr }}
              </button>
              {% endfor %}
            </div>
          </div>

          <div class="mb-3">
            <label class="form-label">Subject</label>
            <input type="text" name="subject" class="form-control" dir="auto"
                   value="{{ form.subject|default:'' }}" required>
          </div>

          <div class="mb-3">
            <label class="form-label">Header title</label>
            <input type="text" name="header_title" class="form-control" dir="auto"
                   value="{{ form.header_title|default:'' }}" placeholder="e.g. Salesforce Login Issue" required>
            <div class="form-text">The large heading in the purple email header.</div>
          </div>

          <div class="mb-3">
            <label class="form-label">Sub-line <span class="text-muted">(optional)</span></label>
            <input type="text" name="sub_line" class="form-control" dir="auto"
                   value="{{ form.sub_line|default:'' }}" placeholder="Small text under the title">
          </div>

          <div class="mb-3">
            <label class="form-label">Body</label>
            <textarea name="body" class="form-control" dir="auto" rows="10"
                      placeholder="Write your message. Leave a blank line between paragraphs." required>{{ form.body|default:'' }}</textarea>
            <div class="form-text">Plain text. Blank lines become paragraphs; single line breaks are kept.</div>
          </div>

          <button type="submit" class="btn btn-primary">
            <i class="bi bi-send me-1"></i>Send
          </button>
        </form>
      </div>
    </div>
  </div>

  <!-- Live preview -->
  <div class="col-lg-6">
    <div class="card mb-3" style="position:sticky;top:1rem;">
      <div class="card-header"><i class="bi bi-eye me-1"></i>Live Preview</div>
      <div class="card-body p-0">
        <iframe id="previewFrame" title="Email preview"
                style="width:100%;border:none;display:block;min-height:640px;background:#f0f0f0;border-radius:0 0 10px 10px;"></iframe>
      </div>
    </div>
  </div>
</div>

<script>
(function () {
  const form  = document.getElementById('broadcastForm');
  const frame = document.getElementById('previewFrame');
  const csrf  = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const previewUrl = '{% url 'broadcast_email' %}?preview=1';
  let timer = null;

  function refresh() {
    const fd = new FormData();
    fd.append('header_title', form.header_title.value);
    fd.append('sub_line', form.sub_line.value);
    fd.append('body', form.body.value);
    fetch(previewUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf }, body: fd })
      .then(function (r) { return r.text(); })
      .then(function (html) { frame.srcdoc = html; })
      .catch(function () { /* leave last good preview on transient error */ });
  }

  function debounced() { clearTimeout(timer); timer = setTimeout(refresh, 400); }

  ['header_title', 'sub_line', 'body'].forEach(function (name) {
    form[name].addEventListener('input', debounced);
  });

  // Quick-pick chips: append the address to the target field (To or Bcc).
  document.querySelectorAll('.quick-chip').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const field = form[btn.dataset.target];
      const addr  = btn.dataset.addr;
      const current = field.value.trim();
      const list = current ? current.split(/[,;]+/).map(function (s) { return s.trim(); }).filter(Boolean) : [];
      const exists = list.some(function (a) { return a.toLowerCase() === addr.toLowerCase(); });
      if (!exists) {
        list.push(addr);
        field.value = list.join(', ');
      }
    });
  });

  refresh();  // initial render
})();
</script>
{% endblock %}
```

- [ ] **Step 2: Verify the template renders (manual)**

Handled in Task 7's manual verification (needs a running server + superuser login).

- [ ] **Step 3: Commit**

```bash
git add templates/tickets/broadcast.html
git commit -m "feat: add broadcast compose + live-preview page"
```

---

## Task 7: Manual verification

**Files:** none (verification only)

This project has no e2e/browser test harness, so verify the wired-up feature by hand. Requires the local stack running and a superuser login. **Do not run the local stack against production email credentials** (per project rule — it would steal live emails); use a local/dev config or a mailbox-safe environment.

- [ ] **Step 1: Static + unit checks**

Run:
```bash
python manage.py check
python -m unittest tickets.test_broadcast_utils integrations.test_graph_recipients -v
```
Expected: check passes with no issues; all unit tests PASS.

- [ ] **Step 2: Access control**

- Log in as a **non-superuser** and GET `/broadcast/` → expect **403 Forbidden**.
- Log in as a **superuser** → the **Broadcast** link appears in the sidebar and the page loads.

- [ ] **Step 3: Live preview**

- Type a header title, sub-line, and multi-paragraph body.
- Confirm the right-hand iframe updates (within ~0.4s of typing) and shows the branded Kramer email (purple header, "IT Support" eyebrow, logo, dark footer) with your text as paragraphs.

- [ ] **Step 4: Quick chips + validation**

- Click a **Bcc** quick chip → the address appears in the Bcc field; click again → no duplicate.
- Submit with an empty Subject/Body → inline error messages; nothing sent.
- Enter a malformed address (e.g. `nope`) in To → "Invalid email address(es): nope"; nothing sent.

- [ ] **Step 5: Send to yourself (real send)**

- To: your own address. Subject + title + body filled. Click **Send**.
- Expect the green "Email sent to 1 recipient(s)." message and the branded email in your inbox.

- [ ] **Step 6: Bcc-only loop-safety check**

- Leave **To empty**, put **your own address in Bcc**, send.
- Confirm: (a) you receive it via Bcc; (b) the To line shows `servicedesk@kramerav.com`; (c) **no new ticket is created** in Kdesk (the self-email guard drops the servicedesk self-copy). If polling is enabled in the test env, check the poller log for `Skipping self-email (from=servicedesk)`.

- [ ] **Step 7: Final commit (if any fixes were needed)**

```bash
git add -A
git commit -m "fix: broadcast email adjustments from manual verification"
```

---

## Notes for the implementer

- **No production email from a dev box.** If the local stack shares production Graph credentials, sending/preview will hit the live servicedesk mailbox. Verify against a safe config, or do the real-send steps (5-6) on the deployed app after the branch is merged and deployed.
- **Deploy** is Docker build → push to ACR → restart the 3 app services (ZipDeploy does not work) — out of scope for this plan but required before the feature is live in production.
