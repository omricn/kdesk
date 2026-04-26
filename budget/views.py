import json
import logging

from django.contrib import messages
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)


# ── Excel → HTML parser ───────────────────────────────────────────────────────

# Office default theme color palette (indices 0–9)
_THEME_COLORS = [
    'FFFFFF', '000000', 'E7E6E6', '44546A',
    '4472C4', 'ED7D31', 'A5A5A5', 'FFC000',
    '5B9BD5', '70AD47',
]

_BORDER_STYLES = {
    'thin': '1px solid', 'medium': '2px solid', 'thick': '3px solid',
    'hair': '1px solid', 'dashed': '1px dashed', 'dotted': '1px dotted',
    'double': '3px double', 'mediumDashed': '2px dashed',
    'dashDot': '1px dashed', 'slantDashDot': '1px dashed',
}


def _tint(hex6, tint):
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    if tint >= 0:
        r, g, b = (round(x + (255 - x) * tint) for x in (r, g, b))
    else:
        r, g, b = (round(x * (1 + tint)) for x in (r, g, b))
    return '{:02X}{:02X}{:02X}'.format(
        min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b))
    )


def _color_css(c):
    if c is None:
        return None
    try:
        t = getattr(c, 'tint', 0) or 0
        if c.type == 'rgb':
            argb = c.rgb or '00000000'
            if len(argb) < 8 or argb[:2] == '00':
                return None
            hex6 = argb[2:8].upper()
            return f'#{_tint(hex6, t) if t else hex6}'
        if c.type == 'theme':
            idx = getattr(c, 'theme', 0) or 0
            if 0 <= idx < len(_THEME_COLORS):
                hex6 = _THEME_COLORS[idx]
                return f'#{_tint(hex6, t) if t else hex6}'
    except Exception:
        pass
    return None


def _border_css(side):
    if not side or not getattr(side, 'border_style', None):
        return None
    style = _BORDER_STYLES.get(side.border_style, '1px solid')
    color = _color_css(getattr(side, 'color', None)) or '#adb5bd'
    return f'{style} {color}'


def _fmt_value(cell):
    from django.utils.html import escape
    from datetime import datetime, date as date_type

    v = cell.value
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, (int, float)):
        nf = (cell.number_format or '').strip()
        if '%' in nf:
            return f'{v * 100:.1f}%'
        if any(s in nf for s in ('$', '€', '£', '₪', r'\$')):
            return f'{v:,.2f}'
        if '#,##' in nf or '_(' in nf:
            return (f'{int(v):,}' if v == int(v) else f'{v:,.2f}')
        return f'{v:g}'
    if isinstance(v, (datetime, date_type)):
        return v.strftime('%d/%m/%Y')
    return escape(str(v))


def excel_to_sheets_html(file_obj):
    """Parse workbook; return list of {name, html} for every visible sheet.

    Uses read_only=True so openpyxl skips pivot table / pivot cache XML entirely,
    which is what causes hangs on files with PivotTables.
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    result = []

    for ws in wb.worksheets:
        buf = ['<table class="budget-table"><tbody>']
        has_content = False
        empty_streak = 0

        for row in ws.iter_rows(max_row=3000):
            if all(c.value is None for c in row):
                empty_streak += 1
                if empty_streak >= 5 and has_content:
                    break
                continue
            empty_streak = 0
            has_content = True

            buf.append('<tr>')
            for cell in row:
                styles = []

                try:
                    fill = cell.fill
                    if fill and fill.fill_type not in (None, 'none'):
                        bg = _color_css(fill.fgColor)
                        if bg and bg.upper() != '#FFFFFF':
                            styles.append(f'background-color:{bg};')
                except Exception:
                    pass

                try:
                    font = cell.font
                    if font:
                        if font.bold:
                            styles.append('font-weight:bold;')
                        if font.italic:
                            styles.append('font-style:italic;')
                        if font.underline and font.underline != 'none':
                            styles.append('text-decoration:underline;')
                        fc = _color_css(font.color)
                        if fc and fc.upper() != '#000000':
                            styles.append(f'color:{fc};')
                        if font.size and font.size != 11:
                            styles.append(f'font-size:{font.size}pt;')
                except Exception:
                    pass

                try:
                    al = cell.alignment
                    if al:
                        if al.horizontal in ('center', 'right', 'left'):
                            styles.append(f'text-align:{al.horizontal};')
                        if al.vertical == 'center':
                            styles.append('vertical-align:middle;')
                        elif al.vertical == 'top':
                            styles.append('vertical-align:top;')
                        if al.wrap_text:
                            styles.append('white-space:pre-wrap;word-break:break-word;')
                except Exception:
                    pass

                try:
                    border = cell.border
                    if border:
                        for side_name in ('top', 'right', 'bottom', 'left'):
                            css = _border_css(getattr(border, side_name))
                            if css:
                                styles.append(f'border-{side_name}:{css};')
                except Exception:
                    pass

                style_attr = f' style="{" ".join(styles)}"' if styles else ''
                buf.append(f'<td{style_attr}>{_fmt_value(cell)}</td>')

            buf.append('</tr>')

        buf.append('</tbody></table>')
        if has_content:
            result.append({'name': ws.title, 'html': ''.join(buf)})

    wb.close()
    return result


# ── View ──────────────────────────────────────────────────────────────────────

def budget_view(request):
    from tickets.views import admin_required  # reuse existing decorator logic
    if not request.user.is_authenticated or not request.user.is_superuser:
        from django.contrib import messages as msg
        msg.error(request, 'Access denied.')
        return redirect('dashboard')

    from .models import BudgetFile

    if request.method == 'POST':
        uploaded = request.FILES.get('excel_file')
        if not uploaded:
            messages.error(request, 'No file selected.')
            return redirect('budget')
        if not uploaded.name.lower().endswith(('.xlsx', '.xls')):
            messages.error(request, 'Only .xlsx and .xls files are supported.')
            return redirect('budget')

        # Read bytes into BytesIO so openpyxl can seek (Azure Blob streams are non-seekable)
        import io
        file_bytes = io.BytesIO(uploaded.read())
        try:
            sheets = excel_to_sheets_html(file_bytes)
        except Exception as exc:
            logger.exception('Budget file parse error')
            messages.error(request, f'Could not parse file: {exc}')
            return redirect('budget')

        BudgetFile.objects.all().delete()
        uploaded.seek(0)
        bf = BudgetFile(
            original_name=uploaded.name,
            uploaded_by=request.user,
            rendered_sheets=json.dumps(sheets),
        )
        bf.file.save(uploaded.name, uploaded)
        bf.save()
        messages.success(request, f'"{uploaded.name}" uploaded successfully.')
        return redirect('budget')

    budget_file = BudgetFile.objects.first()
    sheets = []
    if budget_file and budget_file.rendered_sheets:
        try:
            sheets = json.loads(budget_file.rendered_sheets)
        except Exception:
            pass

    return render(request, 'budget/budget.html', {
        'budget_file': budget_file,
        'sheets': sheets,
    })
