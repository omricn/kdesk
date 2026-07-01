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
