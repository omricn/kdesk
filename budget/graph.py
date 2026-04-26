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


def fetch_sheets_html(sharing_url, token=None):
    """
    Read every visible worksheet from a SharePoint Excel file via Graph API.
    Returns list of {name, html} dicts — uses Excel's pre-computed cell text.

    Pass `token` as the logged-in user's delegated access token so SharePoint
    file permissions are enforced (users without access get a 403).
    If token is None, falls back to the app service account (Sites.Read.All).
    """
    if token is None:
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
        name = sheet['name']
        visibility = sheet.get('visibility', 'Visible')
        if visibility == 'VeryHidden':
            logger.info('Budget graph: skipping VeryHidden sheet %s', name)
            continue

        try:
            rng = requests.get(
                f'{GRAPH}/drives/{drive_id}/items/{item_id}'
                f'/workbook/worksheets/{sheet["id"]}/usedRange',
                headers=hdrs,
                params={'$select': 'text,values,rowCount,columnCount,address'},
                timeout=30,
            )
            rng.raise_for_status()
        except Exception as exc:
            logger.warning('Budget graph: usedRange failed for sheet %s: %s', name, exc)
            result.append({'name': name, 'html': '<p class="text-muted small p-2">Could not load sheet data.</p>'})
            continue

        data = rng.json()
        row_count = data.get('rowCount', 0)
        logger.info('Budget graph: sheet "%s" address=%s rowCount=%s',
                    name, data.get('address'), row_count)

        rows = data.get('text') or data.get('values') or []

        # usedRange sometimes reports rowCount=0 even when the sheet has data
        # (stale Excel metadata). Fall back to reading a broad fixed range.
        if not rows or row_count == 0:
            logger.info('Budget graph: usedRange empty for "%s", trying fixed range', name)
            try:
                fb = requests.get(
                    f'{GRAPH}/drives/{drive_id}/items/{item_id}'
                    f'/workbook/worksheets/{sheet["id"]}/range(address=\'A1:AZ2000\')',
                    headers=hdrs,
                    params={'$select': 'text,values,rowCount'},
                    timeout=30,
                )
                fb.raise_for_status()
                fb_data = fb.json()
                rows = fb_data.get('text') or fb_data.get('values') or []
                logger.info('Budget graph: fixed range for "%s" rowCount=%s',
                            name, fb_data.get('rowCount'))
            except Exception as exc:
                logger.warning('Budget graph: fixed range also failed for "%s": %s', name, exc)

        result.append({'name': name, 'html': _to_html(rows) if rows else '<p class="text-muted small p-2">Empty sheet.</p>'})

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
