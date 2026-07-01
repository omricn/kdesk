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
_SPLIT_RE = re.compile(r'[,;\r\n]+')


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


# ── Rich-body support (inline images pasted into the Broadcast editor) ─────────

# Allowlist for the contenteditable Broadcast body. Mirrors the email-poller
# sanitizer but also permits the `data:` scheme on <img> so pasted screenshots
# survive as base64 during editing/preview (they are converted to CID inline
# attachments at send time).
_BODY_ALLOWED_TAGS = {
    'a', 'b', 'i', 'strong', 'em', 'u', 's', 'br', 'p', 'div', 'span',
    'ul', 'ol', 'li', 'h3', 'h4', 'blockquote', 'img',
}
_BODY_ALLOWED_ATTRS = {
    'a':    {'href', 'title', 'target'},
    'img':  {'src', 'alt', 'width', 'height', 'style'},
    'div':  {'style'},
    'span': {'style'},
    'p':    {'style'},
}

_DATA_IMG_RE = re.compile(
    r'src=(["\'])data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]+?)\1',
    re.IGNORECASE,
)


def sanitize_broadcast_html(raw_html):
    """Sanitize contenteditable HTML from the Broadcast composer to a safe subset.

    Keeps basic formatting and inline images (http/https/data/cid schemes) and
    strips scripts, event handlers, and any other unsafe markup.
    """
    if not raw_html:
        return ''
    import nh3
    return nh3.clean(
        raw_html,
        tags=_BODY_ALLOWED_TAGS,
        attributes=_BODY_ALLOWED_ATTRS,
        url_schemes={'http', 'https', 'mailto', 'cid', 'data'},
        link_rel=None,
    )


def html_text_content(raw_html):
    """Return the visible text of HTML with all tags removed (whitespace-collapsed).

    Used to validate that a body has real content and to store a plain-text
    fallback alongside the rich HTML.
    """
    if not raw_html:
        return ''
    import nh3
    stripped = nh3.clean(raw_html, tags=set(), attributes={})
    return html.unescape(re.sub(r'\s+', ' ', stripped)).strip()


def extract_inline_images(sanitized_html):
    """Convert base64 ``data:`` images in the body to CID inline attachments.

    Returns a tuple ``(rewritten_html, images)`` where each ``data:`` image src
    has been replaced with ``cid:<content_id>`` and ``images`` is a list of dicts
    suitable for ``GraphClient.send_email(inline_images=...)``.
    """
    import base64

    images = []

    def _repl(match):
        quote, subtype, b64 = match.group(1), match.group(2).lower(), match.group(3)
        try:
            content_bytes = base64.b64decode(re.sub(r'\s+', '', b64))
        except Exception:
            return match.group(0)  # leave malformed data-URI untouched
        idx = len(images)
        content_id = f'bc-img-{idx}'
        ext = 'jpg' if subtype in ('jpeg', 'jpg') else subtype
        images.append({
            'content_id':    content_id,
            'name':          f'image-{idx}.{ext}',
            'content_bytes': content_bytes,
            'content_type':  f'image/{"jpeg" if subtype == "jpg" else subtype}',
        })
        return f'src={quote}cid:{content_id}{quote}'

    rewritten = _DATA_IMG_RE.sub(_repl, sanitized_html)
    return rewritten, images
