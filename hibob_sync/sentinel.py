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
    checks.append(_check(
        'request_completed', 'Request marked completed',
        'pass' if req.status == 'completed' else 'fail', f'status={req.status}',
    ))

    user = None
    try:
        user = graph.get_user(req.work_email)
        user_status = 'fail' if user is None else ('pass' if user.get('accountEnabled') else 'fail')
    except Exception as exc:
        checks.append(_check('entra_user', 'Entra account exists and enabled', 'unknown', str(exc)))
        checks.append(_check('m365_groups', 'All M365 groups assigned', 'unknown', 'user lookup failed'))
        checks.append(_check('mailbox', 'Mailbox provisioned', 'unknown', 'user lookup failed'))
        checks.append(_check('creds_email', 'Credentials stored + manager notified',
                             'pass' if (req.manager_email or '').strip() else 'fail'))
        checks.extend(_provisioning_downstream_checks(req))
        return checks
    checks.append(_check('entra_user', 'Entra account exists and enabled', user_status,
                         '' if user_status == 'pass' else f'user={req.work_email!r}'))

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
        # The account exists in Entra; the mailbox/proxyAddresses can lag a little
        # behind provisioning. Treat "not yet present" as UNKNOWN (the sweep will
        # re-check) rather than FAIL, so a slow mailbox never triggers a false
        # escalation email.
        has_mail = bool(user.get('mail')) or bool(user.get('proxyAddresses'))
        checks.append(_check('mailbox', 'Mailbox provisioned', 'pass' if has_mail else 'unknown',
                             '' if has_mail else 'no mail/proxyAddresses yet (may still be provisioning) — will re-check'))

    checks.append(_check(
        'creds_email', 'Credentials stored + manager notified',
        'pass' if (req.manager_email or '').strip() else 'fail',
        '' if (req.manager_email or '').strip() else 'manager_email empty (store-credentials never ran)',
    ))
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

    full_name = (req.employee_name or '').strip()
    for system in ('Priority', 'Salesforce'):
        exists = Ticket.objects.filter(
            subcategory__name=system,
            title__istartswith='TERMINATE USER',
            title__icontains=full_name,
        ).exists() if full_name else False
        checks.append(_check(
            f'term_ticket_{system.lower()}', f'{system} terminate ticket created',
            'pass' if exists else 'fail',
            '' if exists else f'no TERMINATE USER {system} ticket for {full_name!r}',
        ))

    onedrive_ok = bool(getattr(req, 'onedrive_email_sent', False))
    checks.append(_check('onedrive_handover', 'OneDrive handover email sent',
                         'pass' if onedrive_ok else 'fail',
                         '' if onedrive_ok else 'handover email not recorded'))
    return checks
