"""The акт сверки as an Excel file, in the usual two-sided form.

Our side is filled in from the statement (ledger.Statement); the partner's side is
left empty for its accountant to fill in, then both sign. Debit and credit are from
our side: debit, the partner owes us more; credit, less.
"""

from io import BytesIO

import openpyxl
from django.utils.translation import gettext as _
from openpyxl.styles import Alignment, Border, Font, Side

from .ledger import ZERO, Statement

MONEY_FORMAT = "#,##0.00"
THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _date(value) -> str:
    return value.strftime("%d.%m.%Y") if value else "…"


def akt_bytes(statement: Statement, ours) -> bytes:
    """The workbook of the акт сверки between our company ``ours`` and the statement's partner."""
    partner = statement.partner
    our_name, partner_name = ours.name if ours else _("our company"), partner.name
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = _("Reconciliation statement")[:31]
    for column, width in zip("ABCDEFGH", (12, 40, 16, 16, 12, 30, 16, 16), strict=True):
        sheet.column_dimensions[column].width = width

    def title(row, text, size=11, bold=False):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        cell = sheet.cell(row, 1, text)
        cell.font = Font(bold=bold, size=size)
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    title(1, _("RECONCILIATION STATEMENT"), size=14, bold=True)
    title(2, _("of mutual settlements for the period from %(start)s to %(end)s")
          % {"start": _date(statement.date_from), "end": _date(statement.date_to)})  # fmt: skip
    title(3, _("between %(ours)s and %(partner)s") % {"ours": our_name, "partner": partner_name})
    sheet.row_dimensions[3].height = 30
    if partner.inn:
        title(4, _("Partner's INN: %(inn)s") % {"inn": partner.inn})

    row = 6
    for first, name in ((1, our_name), (5, partner_name)):
        sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=first + 3)
        cell = sheet.cell(row, first, _("According to %(name)s, UZS") % {"name": name})
        cell.font, cell.alignment = Font(bold=True), Alignment(horizontal="center", wrap_text=True)
    sheet.row_dimensions[row].height = 30
    row += 1
    for column, header in enumerate([_("Date"), _("Document"), _("Debit"), _("Credit")] * 2, start=1):
        cell = sheet.cell(row, column, header)
        cell.font, cell.alignment = Font(bold=True), Alignment(horizontal="center")

    def line(values, bold=False):
        nonlocal row
        row += 1
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row, column, value if value not in (None, ZERO) or column in (1, 2) else None)
            if column in (3, 4, 7, 8):
                cell.number_format = MONEY_FORMAT
            cell.alignment = Alignment(vertical="top", wrap_text=column in (2, 6))
            if bold:
                cell.font = Font(bold=True)

    def balance_line(date, text, amount):
        line([date, text, amount if amount > 0 else None, -amount if amount < 0 else None, "", text], bold=True)

    balance_line(_date(statement.date_from), _("Balance at the start"), statement.opening)
    for entry in statement.entries:
        text = entry.text if entry.company == partner else f"{entry.text}, {entry.company.name}"
        line([_date(entry.date), text, entry.debit, entry.credit])
    line(["", _("Turnover for the period"), statement.debit, statement.credit, "", _("Turnover for the period")],
         bold=True)  # fmt: skip
    balance_line(_date(statement.date_to), _("Closing balance"), statement.closing)
    for table_row in sheet.iter_rows(min_row=7, max_row=row, max_col=8):
        for cell in table_row:
            cell.border = BOX

    row += 2
    closing = statement.closing
    if closing > 0:
        text = _("According to %(ours)s, on %(date)s %(partner)s owes %(ours)s %(amount)s UZS.")
    elif closing < 0:
        text = _("According to %(ours)s, on %(date)s %(ours)s owes %(partner)s %(amount)s UZS.")
    else:
        text = _("According to %(ours)s, on %(date)s there is no debt between the parties.")
    amount = f"{abs(closing):,.2f}".replace(",", " ").replace(".", ",")
    values = {"ours": our_name, "partner": partner_name, "date": _date(statement.date_to), "amount": amount}
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
    sheet.cell(row, 1, text % values).alignment = Alignment(wrap_text=True)
    sheet.row_dimensions[row].height = 30

    row += 2
    for first, name in ((1, our_name), (5, partner_name)):
        sheet.cell(row, first, _("For %(name)s") % {"name": name}).font = Font(bold=True)
        sheet.cell(row + 2, first, _("Head of the company") + " ____________________")
        sheet.cell(row + 4, first, _("Chief accountant") + " ____________________")
        sheet.cell(row + 6, first, _("Stamp"))
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = "6:7"
    for cells in sheet.iter_rows():
        for cell in cells:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"  # text, never a formula Excel would run
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
