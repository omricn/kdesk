"""
Utilities for the new-employee provisioning pipeline.

- resolve_m365_groups(): looks up the 6 department-specific M365 groups from the Excel table
- parse_hibob_email(): extracts employee fields from the HiBob notification email body
"""
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EXCEL_PATH = Path(__file__).resolve().parent.parent / 'All_365_Groups.xlsx'

# HiBob country name → Excel country code
COUNTRY_NAME_TO_CODE = {
    'israel': 'IL',
    'united states': 'US',
    'usa': 'US',
    'united kingdom': 'UK',
    'uk': 'UK',
    'germany': 'GER',
    'france': 'FRA',
    'india': 'IND',
    'australia': 'AUS',
    'singapore': 'SGP',
    'china': 'CHN',
    'hong kong': 'HK',
    'taiwan': 'TW',
    'korea': 'KOR',
    'south korea': 'KOR',
    'japan': None,  # not in Excel
    'netherlands': 'NLD',
    'sweden': 'SWE',
    'finland': 'FIN',
    'spain': 'ESP',
    'italy': 'ITA',
    'brazil': 'BRA',
    'mexico': 'MEX',
    'canada': 'CAN',
    'argentina': 'ARG',
    'chile': 'CHL',
    'colombia': 'COL',
    'peru': 'PER',
    'new zealand': 'NZL',
    'uae': 'UAE',
    'united arab emirates': 'UAE',
}

# AD OU: full country name used in the OU path
COUNTRY_CODE_TO_OU_NAME = {
    'IL': 'Israel',
    'US': 'United States',
    'UK': 'United Kingdom',
    'GER': 'Germany',
    'FRA': 'France',
    'IND': 'India',
    'AUS': 'Australia',
    'SGP': 'Singapore',
    'CHN': 'China',
    'HK': 'Hong Kong',
    'TW': 'Taiwan',
    'KOR': 'Korea',
    'NLD': 'Netherlands',
    'SWE': 'Sweden',
    'FIN': 'Finland',
    'ESP': 'Spain',
    'ITA': 'Italy',
    'BRA': 'Brazil',
    'MEX': 'Mexico',
    'CAN': 'Canada',
    'ARG': 'Argentina',
    'CHL': 'Chile',
    'COL': 'Colombia',
    'PER': 'Peru',
    'NZL': 'New Zealand',
    'UAE': 'UAE',
}

# Universal groups every employee receives
UNIVERSAL_M365_GROUPS = [
    'Joiners',
    'Microsoft 365 E5 Users',
]


def resolve_m365_groups(region: str, country: str, division: str, department: str):
    """
    Look up the 6 department-specific M365 groups for an employee.

    Returns (groups, fallback) where:
      - groups is a list of email addresses (may be empty on fallback)
      - fallback is True when no matching row was found
    """
    try:
        import openpyxl
    except ImportError:
        logger.error('[Provisioning] openpyxl not installed — cannot resolve M365 groups')
        return [], True

    country_code = COUNTRY_NAME_TO_CODE.get(country.lower().strip())
    if not country_code:
        logger.warning('[Provisioning] Unknown country %r — cannot resolve M365 groups', country)
        return [], True

    try:
        wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        wb.close()
    except Exception as exc:
        logger.error('[Provisioning] Failed to read Excel: %s', exc)
        return [], True

    region_norm = region.strip()
    division_norm = division.strip()
    department_norm = department.strip()

    for row in rows:
        if len(row) < 10:
            continue
        r_region, r_country, r_division, r_dept = (
            (row[0] or '').strip(),
            (row[1] or '').strip(),
            (row[2] or '').strip(),
            (row[3] or '').strip(),
        )
        if (r_country == country_code
                and r_region.lower() == region_norm.lower()
                and r_division.lower() == division_norm.lower()
                and r_dept.lower() == department_norm.lower()):
            groups = [v for v in row[4:10] if v and str(v).strip()]
            return groups, False

    logger.warning(
        '[Provisioning] No Excel match for region=%r country=%r division=%r dept=%r',
        region, country_code, division, department,
    )
    return [], True


# ---------------------------------------------------------------------------
# Email body parser
# ---------------------------------------------------------------------------

_FIELD_MAP = {
    'first name':              'first_name',
    'middle name':             'middle_name',
    'last name':               'last_name',
    'department':              'department',
    'division':                'division',
    'country':                 'country',
    'region':                  'region',
    'start date':              'start_date_raw',
    'personal mobile':         'personal_mobile',
    'report to':               'reports_to',
    'reports to':              'reports_to',
    'job title':               'job_title',
    'employment type':         'employment_type',
    'employee id':             'employee_id',
    'priority':                'priority_raw',
    'priority permissions as': 'priority_permissions_as',
    'salesforce':              'salesforce_raw',
    'country permission':      'country_permission',
}

_HIBOB_SUBJECT_RE = re.compile(
    r'new employee form is pending for your action',
    re.IGNORECASE,
)


def is_hibob_new_employee_email(msg: dict) -> bool:
    subject = msg.get('subject', '')
    sender_email = msg.get('from', {}).get('emailAddress', {}).get('address', '').lower()
    return bool(_HIBOB_SUBJECT_RE.search(subject)) and 'hibob.com' in sender_email


def parse_hibob_email_body(body: str, is_html: bool) -> dict:
    """
    Parse the HiBob new-employee notification body and return a dict of employee fields.
    Handles both HTML and plain-text bodies.
    """
    if is_html:
        # Strip tags, decode entities
        text = re.sub(r'<br\s*/?>', '\n', body, flags=re.IGNORECASE)
        text = re.sub(r'</(p|div|li|tr)>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        import html as _html
        text = _html.unescape(text)
    else:
        text = body

    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key_norm = key.strip().lower()
        value = value.strip()
        if key_norm in _FIELD_MAP:
            dest = _FIELD_MAP[key_norm]
            if dest not in fields:  # first occurrence wins — ignore quoted/repeated lines
                fields[dest] = value

    # Parse start date (DD/MM/YYYY or YYYY-MM-DD, with optional time component)
    start_date = None
    raw = fields.pop('start_date_raw', '')
    if raw:
        raw_date = raw.split()[0]  # strip any trailing time component (e.g. "01/06/2025 00:00:00")
        for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%m/%d/%Y'):
            try:
                start_date = datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                pass

    return {
        'first_name':                  fields.get('first_name', ''),
        'last_name':                   fields.get('last_name', ''),
        'middle_name':                 fields.get('middle_name', ''),
        'department':                  fields.get('department', ''),
        'division':                    fields.get('division', ''),
        'country':                     fields.get('country', ''),
        'region':                      fields.get('region', ''),
        'personal_mobile':             fields.get('personal_mobile', ''),
        'reports_to':                  fields.get('reports_to', ''),
        'job_title':                   fields.get('job_title', ''),
        'employment_type':             fields.get('employment_type', ''),
        'employee_id':                 fields.get('employee_id', ''),
        'start_date':                  start_date,
        'create_priority_ticket':      fields.get('priority_raw', '').strip().lower() == 'yes',
        'priority_permissions_as':     fields.get('priority_permissions_as', ''),
        'create_salesforce_ticket':    fields.get('salesforce_raw', '').strip().lower() == 'yes',
        'salesforce_country_permission': fields.get('country_permission', ''),
    }
