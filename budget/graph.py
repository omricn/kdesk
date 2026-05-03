import base64
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH = 'https://graph.microsoft.com/v1.0'
DASHBOARD_SHEET = 'IT'

# 0-based column indices for the IT sheet dashboard
_DASH_COLS = {
    'E': 4, 'G': 6, 'H': 7, 'I': 8,
    'J': 9, 'K': 10, 'L': 11, 'O': 14,
    'Q': 16, 'R': 17,
}


def _parse_amount(text):
    try:
        return float(str(text).strip().replace(',', '').replace(' ', '') or 0)
    except (ValueError, TypeError):
        return 0.0


def _fmt_amount(v):
    return f'{v:,.0f}' if v else '—'


def parse_dashboard_data(rows):
    if not rows or len(rows) < 2:
        return None

    header_row = rows[0]

    def _hdr(idx):
        return str(header_row[idx]).strip() if idx < len(header_row) and header_row[idx] else ''

    headers = {k: _hdr(v) for k, v in _DASH_COLS.items()}

    data_rows = []
    for row in rows[1:]:
        if not any(c for c in row):
            continue

        def _cell(idx, r=row):
            return str(r[idx]).strip() if idx < len(r) and r[idx] else ''

        subject = _cell(_DASH_COLS['K'])
        budget = _parse_amount(_cell(_DASH_COLS['Q']))
        actual = _parse_amount(_cell(_DASH_COLS['R']))

        if not subject and budget == 0:
            continue

        pct = min(round(actual / budget * 100) if budget else 0, 999)
        bar_pct = min(pct, 100)
        bar_cls = 'bg-danger' if pct > 100 else ('bg-warning' if pct >= 80 else 'bg-success')

        data_rows.append({
            'E': _cell(_DASH_COLS['E']),
            'G': _cell(_DASH_COLS['G']),
            'H': _cell(_DASH_COLS['H']),
            'I': _cell(_DASH_COLS['I']),
            'J': _cell(_DASH_COLS['J']),
            'K': subject,
            'L': _cell(_DASH_COLS['L']),
            'O': _cell(_DASH_COLS['O']),
            'budget': budget,
            'actual': actual,
            'budget_fmt': _fmt_amount(budget),
            'actual_fmt': _fmt_amount(actual),
            'pct': pct,
            'bar_pct': bar_pct,
            'bar_cls': bar_cls,
        })

    total_budget = sum(r['budget'] for r in data_rows)
    total_actual = sum(r['actual'] for r in data_rows)
    remaining = total_budget - total_actual
    total_pct = round(total_actual / total_budget * 100) if total_budget else 0
    total_bar_cls = 'bg-danger' if total_pct > 100 else ('bg-warning' if total_pct >= 80 else 'bg-success')

    # CAPEX / OPEX aggregates
    capex_rows = [r for r in data_rows if r['E'].lower() == 'capex']
    opex_rows  = [r for r in data_rows if r['E'].lower() == 'opex']
    capex_budget = sum(r['budget'] for r in capex_rows)
    capex_actual = sum(r['actual'] for r in capex_rows)
    opex_budget  = sum(r['budget'] for r in opex_rows)
    opex_actual  = sum(r['actual'] for r in opex_rows)
    capex_remaining  = capex_budget - capex_actual
    opex_remaining   = opex_budget  - opex_actual
    capex_budget_fmt    = _fmt_amount(capex_budget)
    capex_actual_fmt    = _fmt_amount(capex_actual)
    capex_remaining_fmt = _fmt_amount(abs(capex_remaining))
    opex_budget_fmt     = _fmt_amount(opex_budget)
    opex_actual_fmt     = _fmt_amount(opex_actual)
    opex_remaining_fmt  = _fmt_amount(abs(opex_remaining))

    # Budget Category (column H) aggregates — ordered by total budget desc
    cat_map = {}
    for r in data_rows:
        cat = r['H'] or 'Uncategorised'
        if cat not in cat_map:
            cat_map[cat] = {'budget': 0.0, 'actual': 0.0}
        cat_map[cat]['budget'] += r['budget']
        cat_map[cat]['actual'] += r['actual']
    categories_chart = []
    for k, v in sorted(cat_map.items(), key=lambda x: x[1]['budget'], reverse=True):
        b, a = v['budget'], v['actual']
        rem = b - a
        pct = round(a / b * 100) if b else 0
        bar_cls = 'bg-danger' if pct > 100 else ('bg-warning' if pct >= 80 else 'bg-success')
        categories_chart.append({
            'name': k,
            'budget': b,
            'actual': a,
            'remaining': rem,
            'over': rem < 0,
            'pct': min(pct, 999),
            'bar_pct': min(pct, 100),
            'bar_cls': bar_cls,
            'budget_fmt': _fmt_amount(b),
            'actual_fmt': _fmt_amount(a),
            'remaining_fmt': _fmt_amount(abs(rem)),
        })

    return {
        'headers': headers,
        'rows': data_rows,
        'total_budget': total_budget,
        'total_actual': total_actual,
        'remaining': remaining,
        'total_pct': total_pct,
        'total_bar_cls': total_bar_cls,
        'total_budget_fmt': _fmt_amount(total_budget),
        'total_actual_fmt': _fmt_amount(total_actual),
        'remaining_fmt': _fmt_amount(abs(remaining)),
        'over_budget': remaining < 0,
        'capex_budget': capex_budget,
        'capex_actual': capex_actual,
        'capex_budget_fmt': capex_budget_fmt,
        'capex_actual_fmt': capex_actual_fmt,
        'capex_remaining': capex_remaining,
        'capex_remaining_fmt': capex_remaining_fmt,
        'capex_over_budget': capex_remaining < 0,
        'opex_budget': opex_budget,
        'opex_actual': opex_actual,
        'opex_budget_fmt': opex_budget_fmt,
        'opex_actual_fmt': opex_actual_fmt,
        'opex_remaining': opex_remaining,
        'opex_remaining_fmt': opex_remaining_fmt,
        'opex_over_budget': opex_remaining < 0,
        'categories_chart': categories_chart,
    }


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
    Read the IT sheet from a SharePoint Excel file using the Graph workbook API
    with a read-only session. Sessions fix the 'empty worksheets' response that
    the stateless API returns for SharePoint-hosted files.

    Pass `token` as the logged-in user's delegated access token.
    If token is None, falls back to the app service account.
    """
    if token is None:
        token = _token()
    hdrs = {'Authorization': f'Bearer {token}'}

    # Resolve sharing URL → driveId + itemId + SharePoint embed info
    item = requests.get(
        f'{GRAPH}/shares/{_encode_url(sharing_url)}/driveItem',
        headers=hdrs,
        params={'$select': 'id,webUrl,sharepointIds,parentReference,name'},
        timeout=15,
    )
    item.raise_for_status()
    item = item.json()
    drive_id = item['parentReference']['driveId']
    item_id = item['id']
    web_url = item.get('webUrl', '')

    sp_ids = item.get('sharepointIds', {})
    unique_id = sp_ids.get('listItemUniqueId', '')
    site_url = sp_ids.get('siteUrl', '').rstrip('/')
    if unique_id and site_url:
        embed_url = (
            f"{site_url}/_layouts/15/Doc.aspx"
            f"?sourcedoc=%7B{unique_id}%7D"
            f"&action=default&mobileredirect=true&wdEmbedCode=0"
        )
    else:
        embed_url = ''
    logger.info('Budget graph: web_url=%s embed_url=%s', web_url, embed_url)

    base = f'{GRAPH}/drives/{drive_id}/items/{item_id}/workbook'

    # Create a read-only workbook session — stateless calls return empty worksheets
    # for SharePoint-hosted files; a session fixes this.
    session_id = None
    try:
        sess = requests.post(
            f'{base}/createSession',
            headers={**hdrs, 'Content-Type': 'application/json'},
            json={'persistChanges': False},
            timeout=30,
        )
        sess.raise_for_status()
        session_id = sess.json().get('id')
        logger.info('Budget graph: workbook session created')
    except Exception as exc:
        logger.warning('Budget graph: could not create workbook session (%s), continuing without', exc)

    if session_id:
        whdrs = {**hdrs, 'workbook-session-id': session_id}
    else:
        whdrs = hdrs

    try:
        # List worksheets
        ws_resp = requests.get(f'{base}/worksheets', headers=whdrs, timeout=15)
        ws_resp.raise_for_status()
        ws_data = ws_resp.json()
        if 'error' in ws_data:
            raise Exception(f"Worksheets API: {ws_data['error'].get('message', ws_data['error'])}")

        all_sheets = ws_data.get('value', [])
        all_sheet_names = [s['name'] for s in all_sheets]
        logger.info('Budget graph: sheets via API: %s', all_sheet_names)

        it_sheet = next((s for s in all_sheets if s['name'] == DASHBOARD_SHEET), None)

        if not it_sheet:
            return {
                'sheets': [], 'web_url': web_url, 'embed_url': embed_url,
                'available_sheets': all_sheet_names,
            }

        # Address sheet by name — avoids curly-brace ID which requests percent-encodes (%7B…%7D)
        sheet_name = it_sheet['name']  # e.g. 'IT'
        rng = requests.get(
            f"{base}/worksheets('{sheet_name}')/usedRange",
            headers=whdrs,
            timeout=60,
        )
        rng.raise_for_status()
        rng_data = rng.json()
        if 'error' in rng_data:
            raise Exception(f"usedRange API: {rng_data['error'].get('message', rng_data['error'])}")

        rows = rng_data.get('text') or rng_data.get('values') or []
        logger.info('Budget graph: IT sheet rowCount=%s, rows=%d', rng_data.get('rowCount'), len(rows))

        # If usedRange came back empty, try a fixed range as fallback
        if not rows:
            logger.info('Budget graph: usedRange empty, trying fixed range A1:AZ500')
            fb = requests.get(
                f"{base}/worksheets('{sheet_name}')/range(address='A1:AZ500')",
                headers=whdrs,
                timeout=60,
            )
            fb.raise_for_status()
            rows = fb.json().get('text') or fb.json().get('values') or []

        sheet_entry = {'name': DASHBOARD_SHEET, 'html': '', 'dashboard': parse_dashboard_data(rows)}
        return {'sheets': [sheet_entry], 'web_url': web_url, 'embed_url': embed_url}

    finally:
        # Always close the session to avoid leaking resources
        if session_id:
            try:
                requests.post(
                    f'{base}/closeSession',
                    headers={**hdrs, 'workbook-session-id': session_id},
                    timeout=10,
                )
            except Exception:
                pass


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
