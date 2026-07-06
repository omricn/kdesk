"""Sentinel P2 — optional LLM (Claude) root-cause diagnosis for escalations.

Degrades gracefully: returns '' when ANTHROPIC_API_KEY is unset or the call
fails, so escalation still works without the AI layer.
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an SRE assistant for an employee-provisioning pipeline (Kdesk + an "
    "on-prem 'KAPPIT' PowerShell agent that does AD/M365 work). Given a failed "
    "verification, respond with a concise root-cause diagnosis and a specific "
    "suggested fix. Be concrete. If the evidence is insufficient, say what to check. "
    "Classify the issue as OPERATIONAL (re-runnable/config) or CODE (needs a code/script "
    "change). Keep it under ~200 words, plain text."
)


def diagnose(kind, req, checks, run_log):
    """Return a short Claude-written diagnosis, or '' if unavailable/misconfigured."""
    if not getattr(settings, 'ANTHROPIC_API_KEY', ''):
        return ''
    failed = [c for c in (checks or []) if c.get('status') in ('fail', 'unknown')]
    name = f"{getattr(req, 'first_name', '')} {getattr(req, 'last_name', '')}".strip() \
        or getattr(req, 'employee_name', '') or f'request #{getattr(req, "id", "?")}'
    lines = [
        f"Provisioning kind: {kind}",
        f"Employee/request: {name}",
        f"Request status: {getattr(req, 'status', '?')}",
        "Failed/unknown checks:",
    ]
    for c in failed:
        lines.append(f"  - {c.get('label')}: {c.get('status')} — {c.get('detail','')}")
    log = (run_log or '').strip()
    if log:
        lines.append("\nKAPPIT run log (tail):\n" + log[-4000:])
    else:
        lines.append("\n(No run log was captured — the run may never have reported.)")
    prompt = "\n".join(lines)
    try:
        return _call_claude(_SYSTEM_PROMPT, prompt)
    except Exception as exc:
        logger.warning('[Sentinel] LLM diagnosis failed: %s', exc)
        return ''


def _call_claude(system_prompt, user_prompt):
    """Isolated so tests can patch it without importing the anthropic package."""
    import anthropic
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
