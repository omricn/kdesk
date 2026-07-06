"""Sentinel P4 — file a GitHub incident issue for an escalated verification.

No-ops when GITHUB_TOKEN is unset. The issue is the phone-reviewable handoff:
title + the LLM diagnosis + failed checks + run-log tail.
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def already_filed(kind, req):
    """True if any prior verification for this request already filed an issue."""
    rel = req.verifications.exclude(issue_url='').exists()
    return rel


def open_incident_issue(kind, req, vr):
    """Open a GitHub issue for this escalation; return the issue URL or ''."""
    token = getattr(settings, 'GITHUB_TOKEN', '')
    repo = getattr(settings, 'GITHUB_REPO', '')
    if not token or not repo:
        return ''
    name = f"{getattr(req, 'first_name', '')} {getattr(req, 'last_name', '')}".strip() \
        or getattr(req, 'employee_name', '') or f'request #{getattr(req, "id", "?")}'
    failed = [c for c in (vr.checks or []) if c.get('status') in ('fail', 'unknown')]
    title = f"[Sentinel] {kind} verification needs attention — {name}"
    body_lines = [
        f"**Automated provisioning-oversight escalation** ({kind}).",
        f"- Employee/request: **{name}**",
        f"- Request id: `{getattr(req, 'id', '?')}`  ·  status: `{getattr(req, 'status', '?')}`",
        "",
        "### Failed / unknown checks",
    ]
    for c in failed:
        body_lines.append(f"- **{c.get('label')}**: {c.get('status')} — {c.get('detail','')}")
    if vr.diagnosis:
        body_lines += ["", "### AI diagnosis", vr.diagnosis]
    log = (getattr(req, 'result_log', '') or '').strip()
    if log:
        tail = log[-4000:].replace("```", "ʼʼʼ")   # neutralize fence-breaking sequences
        body_lines += ["", "### KAPPIT run log (tail)", "```", tail, "```"]
    body = "\n".join(body_lines)
    try:
        return _create_issue(token, repo, title, body, labels=['sentinel', kind])
    except Exception as exc:
        logger.warning('[Sentinel] Could not file GitHub issue: %s', exc)
        return ''


def _create_issue(token, repo, title, body, labels):
    """Isolated network call — patched in tests."""
    import requests
    resp = requests.post(
        f'https://api.github.com/repos/{repo}/issues',
        headers={'Authorization': f'Bearer {token}',
                 'Accept': 'application/vnd.github+json'},
        json={'title': title, 'body': body, 'labels': labels},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get('html_url', '')
