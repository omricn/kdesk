import base64
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH = 'https://graph.microsoft.com/v1.0'


def _token():
    r = requests.post(
        f'https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/oauth2/v2.0/token',
        data={
            'grant_type': 'client_credentials',
            'client_id': settings.AZURE_CLIENT_ID,
            'client_secret': settings.AZURE_CLIENT_SECRET,
            'scope': 'https://graph.microsoft.com/.default',
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()['access_token']


def _encode_url(url):
    b64 = base64.b64encode(url.encode()).decode().rstrip('=')
    return 'u!' + b64.replace('+', '-').replace('/', '_')


def fetch_sheets_html(sharing_url):
    """
    Read every visible worksheet from a SharePoint Excel file via Graph API.
    Returns list of {name, html} dicts — uses Excel's pre-computed cell text
    (numbers formatted, dates formatted, pivot totals already calculated).
    Raises on any network/auth failure so the caller can show an error.
    """
    token = _token()
    hdrs = {'Authorization': f'Bearer {token}'}

    # Resolve sharing URL → driveId + itemId
    item = requests.get(
        f'{GRAPH}/shares/{_encode_url(sharing_url)}/driveItem',
        headers=hdrs, timeout=15,
    )
    item.raise_for_status()
    item = item.json()
    drive_id = item['parentReference']['driveId']
    item_id = item['id']

    # List worksheets
    ws_resp = requests.get(
        f'{GRAPH}/drives/{drive_id}/items/{item_id}/workbook/worksheets',
        headers=hdrs, timeout=15,
    )
    ws_resp.raise_for_status()

    result = []
    for sheet in ws_resp.json().get('value', []):
        if sheet.get('visibility', 'Visible') != 'Visible':
            continue
        try:
            rng = requests.get(
                f'{GRAPH}/drives/{drive_id}/items/{item_id}'
                f'/workbook/worksheets/{sheet["id"]}/usedRange',
                headers=hdrs,
                params={'$select': 'text,rowCount,columnCount'},
                timeout=30,
            )
            rng.raise_for_status()
        except Exception:
            logger.warning('Budget graph: skipping sheet %s', sheet['name'])
            continue

        rows = rng.json().get('text', [])
        if not rows or not any(any(c for c in row) for row in rows):
            continue

        result.append({'name': sheet['name'], 'html': _to_html(rows)})

    return result


def _to_html(rows):
    from django.utils.html import escape

    # Trim trailing blank rows
    while rows and not any(c for c in rows[-1]):
        rows = rows[:-1]

    buf = ['<table class="budget-table"><tbody>']
    for row in rows:
        if not any(c for c in row):
            continue
        buf.append('<tr>')
        for cell in row:
            buf.append(f'<td>{escape(str(cell)) if cell else ""}</td>')
        buf.append('</tr>')
    buf.append('</tbody></table>')
    return ''.join(buf)
