# Provisioning Sentinel — P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify every completed new-hire and termination request against its intended end-state (M365 groups incl. E5, mailbox, downstream tickets/emails, request reconciliation), auto-fix the Kdesk-direct playbook, and escalate the rest to superusers.

**Architecture:** A Kdesk Celery orchestrator ("Sentinel") runs a read-only check suite (Microsoft Graph app-permissions reads + Kdesk DB) after each agent report and on a periodic sweep, records a `VerificationResult`, applies Kdesk-direct remediations (resend credentials email, create missing Priority/SF ticket, re-queue), and emails `Kdesk_Superusers@` on anything unresolved. M365/on-prem *write* remediations are deferred to P3 (KAPPIT channel); P1 detects + escalates them.

**Tech Stack:** Django, Celery, `integrations.graph_client.GraphClient` (MSAL client-credentials), Bootstrap dashboard.

**Spec:** `docs/superpowers/specs/2026-07-06-provisioning-sentinel-design.md`

---

## File structure

- **Create** `hibob_sync/sentinel.py` — pure check functions (provisioning + offboarding) given an injected Graph client; returns lists of check dicts. No side effects.
- **Modify** `integrations/graph_client.py` — add read methods `get_user(upn)` and `get_user_group_identifiers(upn)`.
- **Modify** `hibob_sync/models.py` — add `VerificationResult` model. **Create** migration.
- **Modify** `tasks/scheduled.py` — add `run_sentinel_verification` Celery task (orchestration + Kdesk-direct playbook + escalation email), register a periodic `Sentinel Sweep`.
- **Modify** `hibob_sync/views.py` — enqueue verification from `api_provisioning_report` / `api_offboarding_report` success paths.
- **Modify** `templates/hibob_sync/dashboard.html` — verification badge + expandable checklist + remediation/audit per row.
- **Create** `hibob_sync/test_sentinel.py` — unit tests for checks (fake Graph) and the task (mocked).

A check dict is the shared contract across all tasks:
```python
{"key": "m365_groups", "label": "All M365 groups assigned", "status": "pass"|"fail"|"unknown", "detail": "..."}
```

---

## Task 1: VerificationResult model + migration

**Files:**
- Modify: `hibob_sync/models.py`
- Create: `hibob_sync/migrations/0011_verificationresult.py` (use the next real number)
- Test: `hibob_sync/test_sentinel.py`

- [ ] **Step 1: Write the failing test**

Create `hibob_sync/test_sentinel.py`:
```python
from django.test import TestCase
from hibob_sync.models import ProvisioningRequest, VerificationResult


class VerificationResultModelTests(TestCase):
    def test_create_and_defaults(self):
        req = ProvisioningRequest.objects.create(
            first_name='Luis', last_name='Diaz', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='completed',
        )
        vr = VerificationResult.objects.create(
            kind='provisioning', provisioning_request=req, overall='pending',
            checks=[{'key': 'x', 'label': 'X', 'status': 'pass', 'detail': ''}],
        )
        self.assertEqual(vr.overall, 'pending')
        self.assertEqual(vr.attempts, 0)
        self.assertEqual(vr.remediations, [])
        self.assertEqual(req.verifications.first(), vr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hibob_sync.test_sentinel.VerificationResultModelTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'VerificationResult'`.

- [ ] **Step 3: Add the model**

Append to `hibob_sync/models.py`:
```python
class VerificationResult(models.Model):
    """Sentinel oversight record for one provisioning/offboarding request."""
    KIND_CHOICES = [('provisioning', 'Provisioning'), ('offboarding', 'Offboarding')]
    OVERALL_CHOICES = [
        ('pending', 'Pending'), ('ok', 'OK'), ('remediated', 'Remediated'),
        ('escalated', 'Escalated'), ('failed', 'Failed'),
    ]
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    provisioning_request = models.ForeignKey(
        'ProvisioningRequest', null=True, blank=True, on_delete=models.CASCADE,
        related_name='verifications',
    )
    offboarding_request = models.ForeignKey(
        'OffboardingRequest', null=True, blank=True, on_delete=models.CASCADE,
        related_name='verifications',
    )
    overall = models.CharField(max_length=20, choices=OVERALL_CHOICES, default='pending')
    checks = models.JSONField(default=list)        # list of {key,label,status,detail}
    remediations = models.JSONField(default=list)  # list of {action,target,result,at}
    attempts = models.PositiveSmallIntegerField(default=0)
    diagnosis = models.TextField(blank=True)       # populated in P2 (LLM)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Verification({self.kind} #{self.provisioning_request_id or self.offboarding_request_id}: {self.overall})'
```

- [ ] **Step 4: Create the migration**

Run: `python manage.py makemigrations hibob_sync`
Expected: creates `hibob_sync/migrations/00NN_verificationresult.py`. (If the app can't run locally, hand-write the migration mirroring the field set above with dependency on the latest hibob_sync migration.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test hibob_sync.test_sentinel.VerificationResultModelTests -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add hibob_sync/models.py hibob_sync/migrations/ hibob_sync/test_sentinel.py
git commit -m "feat(sentinel): VerificationResult model"
```

---

## Task 2: GraphClient read methods

**Files:**
- Modify: `integrations/graph_client.py`
- Test: `integrations/test_graph_reads.py` (create)

- [ ] **Step 1: Write the failing test**

Create `integrations/test_graph_reads.py`:
```python
from unittest.mock import patch
from django.test import TestCase
from integrations.graph_client import GraphClient


class GraphReadTests(TestCase):
    def _client(self):
        c = GraphClient.__new__(GraphClient)   # bypass __init__/MSAL
        return c

    def test_get_user_returns_fields(self):
        c = self._client()
        with patch.object(c, 'get', return_value={'id': '1', 'accountEnabled': True, 'mail': 'a@x.com', 'displayName': 'A'}):
            u = c.get_user('a@x.com')
        self.assertTrue(u['accountEnabled'])
        self.assertEqual(u['mail'], 'a@x.com')

    def test_get_user_returns_none_on_404(self):
        import requests
        c = self._client()
        err = requests.exceptions.HTTPError(response=type('R', (), {'status_code': 404})())
        with patch.object(c, 'get', side_effect=err):
            self.assertIsNone(c.get_user('missing@x.com'))

    def test_group_identifiers_lowercased_union_of_mail_and_name(self):
        c = self._client()
        groups = [
            {'id': '1', 'mail': 'CHL_All@x.com', 'displayName': 'CHL All'},
            {'id': '2', 'mail': None, 'displayName': 'Joiners'},
        ]
        with patch.object(c, 'get_paginated', return_value=groups):
            ids = c.get_user_group_identifiers('a@x.com')
        self.assertIn('chl_all@x.com', ids)
        self.assertIn('joiners', ids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test integrations.test_graph_reads -v 2`
Expected: FAIL — `AttributeError: 'GraphClient' object has no attribute 'get_user'`.

- [ ] **Step 3: Add the methods**

In `integrations/graph_client.py`, add to the `GraphClient` class (near the other user/group methods):
```python
    def get_user(self, upn: str):
        """Return {id, accountEnabled, mail, displayName, proxyAddresses} or None if not found."""
        import requests
        try:
            return self.get(
                f'/users/{upn}',
                params={'$select': 'id,accountEnabled,mail,displayName,proxyAddresses'},
            )
        except requests.exceptions.HTTPError as exc:
            if getattr(exc.response, 'status_code', None) == 404:
                return None
            raise

    def get_user_group_identifiers(self, upn: str) -> set:
        """Return a lowercased set of the user's group identifiers (both mail and
        displayName), so membership can be matched whether a group is referenced
        by email (e.g. CHL_All@x.com) or by name (e.g. Joiners)."""
        groups = self.get_paginated(
            f'/users/{upn}/memberOf/microsoft.graph.group',
            params={'$select': 'id,mail,displayName'},
        )
        ids = set()
        for g in groups:
            if g.get('mail'):
                ids.add(g['mail'].strip().lower())
            if g.get('displayName'):
                ids.add(g['displayName'].strip().lower())
        return ids
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test integrations.test_graph_reads -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add integrations/graph_client.py integrations/test_graph_reads.py
git commit -m "feat(sentinel): GraphClient read methods (get_user, group membership)"
```

---

## Task 3: Sentinel check functions

**Files:**
- Create: `hibob_sync/sentinel.py`
- Test: `hibob_sync/test_sentinel.py` (append)

- [ ] **Step 1: Write the failing test** (append to `hibob_sync/test_sentinel.py`)

```python
from hibob_sync import sentinel


class FakeGraph:
    def __init__(self, user, group_ids):
        self._user = user
        self._group_ids = {g.lower() for g in group_ids}
    def get_user(self, upn):
        return self._user
    def get_user_group_identifiers(self, upn):
        return set(self._group_ids)


class ProvisioningChecksTests(TestCase):
    def _req(self, **kw):
        base = dict(
            first_name='Luis', last_name='Diaz', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='completed', work_email='ldiaz@kramerav.com',
            manager_email='mgr@kramerav.com',
            m365_groups=['Joiners', 'Microsoft 365 E5 Users', 'CHL_All@kramerav.com'],
            create_priority_ticket=False, create_salesforce_ticket=False,
        )
        base.update(kw)
        return ProvisioningRequest.objects.create(**base)

    def test_all_pass_when_everything_present(self):
        req = self._req()
        g = FakeGraph({'accountEnabled': True, 'mail': 'ldiaz@kramerav.com'},
                      ['joiners', 'microsoft 365 e5 users', 'chl_all@kramerav.com'])
        checks = sentinel.verify_provisioning_checks(req, g)
        by = {c['key']: c for c in checks}
        self.assertEqual(by['entra_user']['status'], 'pass')
        self.assertEqual(by['m365_groups']['status'], 'pass')
        self.assertEqual(by['mailbox']['status'], 'pass')
        self.assertEqual(by['creds_email']['status'], 'pass')

    def test_missing_group_fails_and_names_it(self):
        req = self._req()
        g = FakeGraph({'accountEnabled': True, 'mail': 'ldiaz@kramerav.com'},
                      ['joiners', 'microsoft 365 e5 users'])   # CHL_All missing
        checks = {c['key']: c for c in sentinel.verify_provisioning_checks(req, g)}
        self.assertEqual(checks['m365_groups']['status'], 'fail')
        self.assertIn('CHL_All@kramerav.com', checks['m365_groups']['detail'])

    def test_missing_user_marks_dependent_checks_unknown(self):
        req = self._req()
        g = FakeGraph(None, [])   # user not in Entra
        checks = {c['key']: c for c in sentinel.verify_provisioning_checks(req, g)}
        self.assertEqual(checks['entra_user']['status'], 'fail')
        self.assertEqual(checks['m365_groups']['status'], 'unknown')

    def test_graph_error_is_unknown_not_fail(self):
        req = self._req()
        class Boom:
            def get_user(self, upn): raise RuntimeError('graph down')
            def get_user_group_identifiers(self, upn): raise RuntimeError('graph down')
        checks = {c['key']: c for c in sentinel.verify_provisioning_checks(req, Boom())}
        self.assertEqual(checks['entra_user']['status'], 'unknown')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hibob_sync.test_sentinel.ProvisioningChecksTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'hibob_sync.sentinel'`.

- [ ] **Step 3: Implement `hibob_sync/sentinel.py`**

```python
"""Sentinel oversight — read-only verification checks for provisioning/offboarding.

Each function takes a request and an injected Graph client (so it is unit-testable
with a fake) and returns a list of check dicts:
    {"key": str, "label": str, "status": "pass"|"fail"|"unknown", "detail": str}
No side effects — remediation and persistence live in the Celery task.
"""


def _check(key, label, status, detail=''):
    return {'key': key, 'label': label, 'status': status, 'detail': detail}


def verify_provisioning_checks(req, graph):
    checks = []

    # 1. Request reached terminal completed state
    checks.append(_check(
        'request_completed', 'Request marked completed',
        'pass' if req.status == 'completed' else 'fail',
        f'status={req.status}',
    ))

    # 2. Entra user exists + enabled
    user = None
    user_status = 'unknown'
    try:
        user = graph.get_user(req.work_email)
        if user is None:
            user_status = 'fail'
        elif user.get('accountEnabled'):
            user_status = 'pass'
        else:
            user_status = 'fail'
    except Exception as exc:  # Graph error → unknown, never a false failure
        checks.append(_check('entra_user', 'Entra account exists and enabled', 'unknown', str(exc)))
        checks.append(_check('m365_groups', 'All M365 groups assigned', 'unknown', 'user lookup failed'))
        checks.append(_check('mailbox', 'Mailbox provisioned', 'unknown', 'user lookup failed'))
        checks.extend(_provisioning_downstream_checks(req))
        return checks
    checks.append(_check('entra_user', 'Entra account exists and enabled', user_status,
                         '' if user_status == 'pass' else f'user={req.work_email!r}'))

    # 3. All resolved M365 groups present (incl. Microsoft 365 E5 Users -> grants E5)
    if user is None:
        checks.append(_check('m365_groups', 'All M365 groups assigned', 'unknown', 'no Entra user'))
        checks.append(_check('mailbox', 'Mailbox provisioned', 'unknown', 'no Entra user'))
    else:
        try:
            member_ids = graph.get_user_group_identifiers(req.work_email)
            wanted = [g for g in (req.m365_groups or []) if g and str(g).strip()]
            missing = [g for g in wanted if g.strip().lower() not in member_ids]
            checks.append(_check(
                'm365_groups', 'All M365 groups assigned',
                'pass' if not missing else 'fail',
                'all present' if not missing else 'missing: ' + ', '.join(missing),
            ))
        except Exception as exc:
            checks.append(_check('m365_groups', 'All M365 groups assigned', 'unknown', str(exc)))

        # 4. Mailbox provisioned (has a mail/proxy address — no extra Graph scope needed)
        has_mail = bool(user.get('mail')) or bool(user.get('proxyAddresses'))
        checks.append(_check('mailbox', 'Mailbox provisioned', 'pass' if has_mail else 'fail',
                             '' if has_mail else 'no mail/proxyAddresses'))

    # 5. Credentials stored + manager email sent (manager_email populated by store-credentials)
    checks.append(_check(
        'creds_email', 'Credentials stored + manager notified',
        'pass' if (req.manager_email or '').strip() else 'fail',
        '' if (req.manager_email or '').strip() else 'manager_email empty (store-credentials never ran)',
    ))

    # 6/7. Downstream Priority/SF tickets (if flagged)
    checks.extend(_provisioning_downstream_checks(req))
    return checks


def _provisioning_downstream_checks(req):
    from tickets.models import Ticket
    out = []
    for flag, system in ((req.create_priority_ticket, 'Priority'),
                         (req.create_salesforce_ticket, 'Salesforce')):
        if not flag:
            continue
        exists = Ticket.objects.filter(
            subcategory__name=system, requester_email__iexact=req.work_email,
            title__startswith='NEW USER',
        ).exists()
        out.append(_check(
            f'ticket_{system.lower()}', f'{system} new-user ticket created',
            'pass' if exists else 'fail',
            '' if exists else f'no NEW USER {system} ticket for {req.work_email}',
        ))
    return out


def verify_offboarding_checks(req, graph):
    from tickets.models import Ticket
    checks = []

    checks.append(_check(
        'request_completed', 'Request marked completed',
        'pass' if req.status == 'completed' else 'fail', f'status={req.status}',
    ))

    # Account disabled in Entra
    try:
        user = graph.get_user(req.employee_email)
        if user is None:
            checks.append(_check('account_disabled', 'Account disabled/removed', 'pass', 'user not found (removed)'))
        else:
            disabled = not user.get('accountEnabled', True)
            checks.append(_check('account_disabled', 'Account disabled', 'pass' if disabled else 'fail',
                                 '' if disabled else 'account still enabled'))
    except Exception as exc:
        checks.append(_check('account_disabled', 'Account disabled', 'unknown', str(exc)))

    # Termination tickets (subject + category are fixed by the offboarding flow)
    full_name = (req.employee_name or '').strip()
    for system, cat_sub in (('Priority', 'Priority'), ('Salesforce', 'SalesForce')):
        exists = Ticket.objects.filter(
            subcategory__name=cat_sub,
            title__istartswith=f'TERMINATE USER',
            title__icontains=full_name,
        ).exists() if full_name else False
        checks.append(_check(
            f'term_ticket_{system.lower()}', f'{system} terminate ticket created',
            'pass' if exists else 'fail',
            '' if exists else f'no TERMINATE USER {system} ticket for {full_name!r}',
        ))

    # OneDrive handover email is the last step; represented on the request once sent.
    onedrive_ok = bool(getattr(req, 'onedrive_email_sent', False))
    checks.append(_check('onedrive_handover', 'OneDrive handover email sent',
                         'pass' if onedrive_ok else 'fail',
                         '' if onedrive_ok else 'handover email not recorded'))
    return checks
```

> **Note for implementer:** `verify_offboarding_checks` references `req.onedrive_email_sent`. Confirm the real signal in the offboarding flow (§14 of the spec). If there's no such field, add a `BooleanField(default=False)` to `OffboardingRequest` set where the handover email is sent, in this same task (add a migration). Do not leave it reading a non-existent attribute.

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hibob_sync.test_sentinel.ProvisioningChecksTests -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hibob_sync/sentinel.py hibob_sync/test_sentinel.py
git commit -m "feat(sentinel): provisioning + offboarding check suites"
```

---

## Task 4: Verification task + Kdesk-direct playbook + escalation

**Files:**
- Modify: `tasks/scheduled.py`
- Test: `hibob_sync/test_sentinel.py` (append)

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import patch, MagicMock


class RunSentinelTests(TestCase):
    def _req(self):
        return ProvisioningRequest.objects.create(
            first_name='A', last_name='B', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='completed', work_email='ab@kramerav.com',
            manager_email='m@kramerav.com', m365_groups=['Joiners'],
        )

    def test_all_pass_sets_ok_no_escalation(self):
        from tasks.scheduled import run_sentinel_verification
        req = self._req()
        checks = [{'key': 'k', 'label': 'K', 'status': 'pass', 'detail': ''}]
        with patch('hibob_sync.sentinel.verify_provisioning_checks', return_value=checks), \
             patch('tasks.scheduled._sentinel_escalate') as esc, \
             patch('integrations.graph_client.get_client', return_value=MagicMock()):
            run_sentinel_verification('provisioning', req.id)
        vr = req.verifications.first()
        self.assertEqual(vr.overall, 'ok')
        esc.assert_not_called()

    def test_fail_escalates_and_records(self):
        from tasks.scheduled import run_sentinel_verification
        req = self._req()
        checks = [{'key': 'entra_user', 'label': 'x', 'status': 'fail', 'detail': 'gone'}]
        with patch('hibob_sync.sentinel.verify_provisioning_checks', return_value=checks), \
             patch('tasks.scheduled._sentinel_escalate') as esc, \
             patch('integrations.graph_client.get_client', return_value=MagicMock()):
            run_sentinel_verification('provisioning', req.id)
        vr = req.verifications.first()
        self.assertEqual(vr.overall, 'escalated')
        esc.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hibob_sync.test_sentinel.RunSentinelTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'run_sentinel_verification'`.

- [ ] **Step 3: Implement the task + helpers** (append to `tasks/scheduled.py`)

```python
@shared_task(name='tasks.run_sentinel_verification')
def run_sentinel_verification(kind, req_id):
    """Verify one completed request, apply the Kdesk-direct remediation playbook,
    and escalate anything unresolved. Read-only checks; safe to re-run."""
    from hibob_sync.models import ProvisioningRequest, OffboardingRequest, VerificationResult
    from hibob_sync import sentinel
    from integrations.graph_client import get_client

    if kind == 'provisioning':
        req = ProvisioningRequest.objects.filter(id=req_id).first()
    else:
        req = OffboardingRequest.objects.filter(id=req_id).first()
    if not req:
        return 'request-not-found'

    graph = get_client()
    if kind == 'provisioning':
        checks = sentinel.verify_provisioning_checks(req, graph)
    else:
        checks = sentinel.verify_offboarding_checks(req, graph)

    vr = VerificationResult(kind=kind, checks=checks, overall='pending')
    if kind == 'provisioning':
        vr.provisioning_request = req
    else:
        vr.offboarding_request = req

    # Kdesk-direct playbook (idempotent). M365/on-prem write fixes are P3.
    remediations = []
    if kind == 'provisioning':
        remediations = _sentinel_kdesk_playbook(req, checks)
        if remediations:
            vr.attempts = 1
            # Re-run downstream checks that we may have fixed (tickets), keep Graph checks as-is.
            fresh = {c['key']: c for c in sentinel.verify_provisioning_checks(req, graph)}
            checks = [fresh.get(c['key'], c) for c in checks]
            vr.checks = checks
    vr.remediations = remediations

    statuses = {c['status'] for c in checks}
    if 'fail' in statuses:
        vr.overall = 'escalated'
    elif 'unknown' in statuses:
        vr.overall = 'remediated' if remediations else 'pending'
    else:
        vr.overall = 'remediated' if remediations else 'ok'
    vr.save()

    if vr.overall == 'escalated':
        _sentinel_escalate(kind, req, vr)
    return vr.overall


def _sentinel_kdesk_playbook(req, checks):
    """Apply only the safe, idempotent Kdesk-side fixes. Returns list of remediation dicts."""
    done = []
    failed = {c['key'] for c in checks if c['status'] == 'fail'}

    # Missing Priority/SF new-user ticket -> re-create via the existing helper.
    if any(k.startswith('ticket_') for k in failed):
        try:
            from hibob_sync.views import _create_system_tickets
            _create_system_tickets(req, req.work_email)
            done.append({'action': 'create_system_tickets', 'target': req.work_email,
                         'result': 'ok', 'at': timezone.now().isoformat()})
        except Exception as exc:
            done.append({'action': 'create_system_tickets', 'target': req.work_email,
                         'result': f'error: {exc}', 'at': timezone.now().isoformat()})
    return done


def _sentinel_escalate(kind, req, vr):
    """Email superusers about an unresolved verification. (P2 adds LLM diagnosis.)"""
    try:
        from integrations.graph_client import get_client
        name = _esc(f'{getattr(req, "first_name", "")} {getattr(req, "last_name", "")}'.strip()
                    or getattr(req, 'employee_name', '') or f'request #{req.id}')
        failed = [c for c in vr.checks if c['status'] in ('fail', 'unknown')]
        rows = ''.join(
            f'<li><strong>{_esc(c["label"])}</strong>: {_esc(c["status"])}'
            + (f' — {_esc(c["detail"])}' if c.get('detail') else '') + '</li>'
            for c in failed
        )
        body_html = _email_html(
            header_title='Provisioning verification needs attention',
            header_subtitle=name,
            greeting=(
                f'The Sentinel verified the {kind} for <strong>{name}</strong> and found '
                f'unresolved issues it could not safely auto-fix:<br><br><ul>{rows}</ul>'
                f'Open the HiBob Sync dashboard to review and act.'
            ),
            body_rows='',
        )
        get_client().send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email='Kdesk_Superusers@kramerav.com',
            subject=f'⚠️ Sentinel: {kind} verification for {name} needs attention',
            body_html=body_html,
        )
    except Exception as exc:
        logger.warning('[Sentinel] Could not send escalation for %s #%s: %s', kind, req.id, exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hibob_sync.test_sentinel.RunSentinelTests -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tasks/scheduled.py hibob_sync/test_sentinel.py
git commit -m "feat(sentinel): verification task, Kdesk-direct playbook, escalation email"
```

---

## Task 5: Wire triggers (report handlers + periodic sweep)

**Files:**
- Modify: `hibob_sync/views.py` (`api_provisioning_report` ~line 461 success branch; `api_offboarding_report` success branch)
- Modify: `tasks/scheduled.py` (`register_periodic_tasks`)

- [ ] **Step 1: Enqueue verification on a successful provisioning report**

In `hibob_sync/views.py`, inside `api_provisioning_report`, in the `elif success and work_email:` branch (right after `_send_provisioning_result_notification(...)`), add:
```python
            from tasks.scheduled import run_sentinel_verification
            run_sentinel_verification.apply_async(('provisioning', req.id), countdown=120)
```
> `countdown=120` gives M365 group/mailbox state a moment to settle before verifying.

- [ ] **Step 2: Enqueue verification on a successful offboarding report**

In `api_offboarding_report`, in the success branch that marks the request completed, add the analogous call:
```python
            from tasks.scheduled import run_sentinel_verification
            run_sentinel_verification.apply_async(('offboarding', req.id), countdown=120)
```

- [ ] **Step 3: Register the periodic sweep**

In `tasks/scheduled.py` `register_periodic_tasks`, add a task that re-verifies recently-completed requests lacking an `ok`/`remediated` verification. First add the sweep task (append near `sweep_stuck_provisioning`):
```python
@shared_task(name='tasks.sentinel_sweep')
def sentinel_sweep():
    """Re-verify recently completed provisioning/offboarding requests that have no
    settled (ok/remediated) verification yet — covers reports that raced or were missed."""
    from datetime import timedelta
    from hibob_sync.models import ProvisioningRequest, OffboardingRequest
    cutoff = timezone.now() - timedelta(days=2)
    n = 0
    for req in ProvisioningRequest.objects.filter(status='completed', completed_at__gte=cutoff):
        if not req.verifications.filter(overall__in=('ok', 'remediated')).exists():
            run_sentinel_verification.delay('provisioning', req.id); n += 1
    for req in OffboardingRequest.objects.filter(status='completed', completed_at__gte=cutoff):
        if not req.verifications.filter(overall__in=('ok', 'remediated')).exists():
            run_sentinel_verification.delay('offboarding', req.id); n += 1
    return n
```
Then register it on the 15-minute interval (add to the `interval_tasks` list):
```python
        ('Sentinel Sweep', 'tasks.sentinel_sweep', sla_interval),
```

- [ ] **Step 4: Verify wiring parses**

Run: `python -c "import ast; ast.parse(open('tasks/scheduled.py',encoding='utf-8').read()); ast.parse(open('hibob_sync/views.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add hibob_sync/views.py tasks/scheduled.py
git commit -m "feat(sentinel): trigger verification on report + 15-min sweep"
```

---

## Task 6: Dashboard badge + checklist + audit

**Files:**
- Modify: `hibob_sync/views.py` (`hibob_sync_dashboard` context — attach latest verification per request)
- Modify: `templates/hibob_sync/dashboard.html`

- [ ] **Step 1: Attach latest verification to dashboard rows**

In `hibob_sync_dashboard`, where provisioning requests are collected for the template, prefetch the latest verification. After the queryset is built, add:
```python
    # Attach the most recent verification to each provisioning request for the badge.
    for pr in provisioning_requests:   # use the actual variable name in this view
        pr.latest_verification = pr.verifications.first()   # ordering = -created_at
```

- [ ] **Step 2: Render the badge + collapsible checklist**

In `templates/hibob_sync/dashboard.html`, in the provisioning row (near the flow badges), add a verification badge cell:
```html
{% with v=pr.latest_verification %}
  {% if v %}
  <span class="badge {% if v.overall == 'ok' %}bg-success{% elif v.overall == 'remediated' %}bg-info text-dark{% elif v.overall == 'escalated' %}bg-warning text-dark{% elif v.overall == 'failed' %}bg-danger{% else %}bg-secondary{% endif %}"
        role="button" data-bs-toggle="collapse" data-bs-target="#verify-{{ pr.id }}"
        title="Sentinel verification">
    <i class="bi bi-shield-check me-1"></i>{{ v.get_overall_display }}
  </span>
  <div class="collapse mt-1" id="verify-{{ pr.id }}">
    <ul class="list-unstyled small mb-1">
      {% for c in v.checks %}
      <li>
        {% if c.status == 'pass' %}<i class="bi bi-check-circle-fill text-success"></i>
        {% elif c.status == 'fail' %}<i class="bi bi-x-circle-fill text-danger"></i>
        {% else %}<i class="bi bi-question-circle-fill text-secondary"></i>{% endif %}
        {{ c.label }}{% if c.detail %} <span class="text-muted">— {{ c.detail }}</span>{% endif %}
      </li>
      {% endfor %}
    </ul>
    {% if v.remediations %}
    <div class="small text-muted"><strong>Auto-fixed:</strong>
      {% for r in v.remediations %}{{ r.action }} ({{ r.result }}){% if not forloop.last %}, {% endif %}{% endfor %}
    </div>
    {% endif %}
  </div>
  {% endif %}
{% endwith %}
```

- [ ] **Step 3: Manual smoke check**

Run locally if possible, or verify on deploy: open the HiBob Sync dashboard, confirm a completed request shows a verification badge; click it to expand the checklist. Expected: badge color matches `overall`, checklist lists each check with an icon.

- [ ] **Step 4: Commit**

```bash
git add hibob_sync/views.py templates/hibob_sync/dashboard.html
git commit -m "feat(sentinel): dashboard verification badge + checklist + audit"
```

---

## Task 7: Deploy & verify end-to-end

- [ ] **Step 1: Run the full hibob_sync test suite**

Run: `python manage.py test hibob_sync integrations.test_graph_reads -v 2`
Expected: all PASS. (If the app can't run locally per the deploy note, rely on the Docker build + a post-deploy smoke check.)

- [ ] **Step 2: Deploy**

Run: `bash deploy.sh` (Docker Desktop must be running). Confirms migrate applies `VerificationResult` and `register_periodic_tasks()` seeds `Sentinel Sweep`.

- [ ] **Step 3: Post-deploy smoke check**

- Trigger (or wait for) a provisioning/offboarding completion, or manually run in a shell: `run_sentinel_verification('provisioning', <id>)`.
- Confirm a `VerificationResult` row is created and the dashboard badge renders.
- Confirm an intentionally-incomplete request escalates an email to `Kdesk_Superusers@`.

- [ ] **Step 4: Commit any fixes and finish**

---

## Self-review notes

- **Spec coverage:** §4 new-hire checks → Task 3 `verify_provisioning_checks`; §4 termination → Task 3 `verify_offboarding_checks`; §5 playbook (Kdesk-direct subset) → Task 4 `_sentinel_kdesk_playbook` (group/license fixes are P3, correctly deferred — detected + escalated in P1); §6 data flow/triggers → Task 5; §4 dashboard/observability → Task 6; §9 error handling (unknown-not-fail, idempotent, decoupled) → Task 3/4.
- **Deferred to P3 (by design):** M365 group re-add + on-prem AD probe (need the KAPPIT `AgentJob` channel). P1 escalates these.
- **Deferred to P2:** LLM `diagnosis` field is present on the model but populated later.
- **Implementer confirmations:** (a) `OffboardingRequest.onedrive_email_sent` signal (Task 3 note); (b) exact variable names in `hibob_sync_dashboard`/report handlers; (c) next migration number.
