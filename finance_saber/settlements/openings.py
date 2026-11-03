"""Partners' opening balances as an Excel file: download the current ones, fill in, load back.

One row per partner: INN, company, date, opening balance (positive: it owes us;
negative: we owe it). A partner is found by INN among head companies, else by name
or a name it goes by in reports (a filial's row counts for its head company).
Loading is all or nothing.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import openpyxl
from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from finance_saber.cashbook.exports import Sheet
from finance_saber.cashbook.handbooks import HandbookError, clean, table
from finance_saber.cashbook.models import Company
from finance_saber.production.importer import key
from finance_saber.production.shipment_import import company_lookup

from . import ledger
from .models import OpeningBalance

COLUMNS = {
    gettext_noop("INN"): "inn",
    gettext_noop("Company"): "name",
    gettext_noop("Date"): "date",
    gettext_noop("Opening balance"): "amount",
    "Сальдо начальное": "amount",  # as 1C and accountants call it
    "Сальдо": "amount",
}


def openings_sheet() -> Sheet:
    """Every partner (we ship to, or with an opening balance) and its opening balance, to fill in and load."""
    openings = {opening.company_id: opening for opening in OpeningBalance.objects.all()}
    rows = []
    for row in ledger.balances():
        opening = openings.get(row.partner.pk)
        date, amount = (opening.date, opening.amount) if opening else (None, None)
        rows.append([row.partner.inn, row.partner.name, date, amount])
    headers = [_(header) for header in COLUMNS]
    return Sheet(_("Opening balances"), headers, rows, money_columns=(3,))


@dataclass
class OpeningImport:
    errors: list[str] = field(default_factory=list)
    saved_count: int = 0
    saved: bool = False


def _date(value) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(clean(value), "%d.%m.%Y").date()
    except ValueError:
        return None


def _amount(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace("\xa0", "").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def load_openings(file, user=None, dry_run=False) -> OpeningImport:
    try:
        workbook = openpyxl.load_workbook(file, data_only=True, read_only=True)
    except Exception as error:  # not a readable .xlsx
        raise HandbookError(_("The file is not a readable .xlsx workbook.")) from error
    try:
        rows = table(workbook.worksheets[0], COLUMNS, ("date", "amount"))
    finally:
        workbook.close()
    by_inn = {company.inn: company for company in Company.objects.heads().exclude(inn="")}
    by_name = company_lookup()
    report = OpeningImport()
    with transaction.atomic():
        for number, row in rows:
            inn, name = clean(row.get("inn")), clean(row.get("name"))
            if row.get("amount") in (None, "") and row.get("date") in (None, ""):
                continue  # an empty row, or a partner listed without an opening balance
            company = by_inn.get(inn) or (by_name.get(key(name)) if name else None)
            date, amount = _date(row["date"]), _amount(row["amount"])
            problems = []
            if company is None:
                problems.append(_("no company with INN «%(inn)s» or name «%(name)s».") % {"inn": inn, "name": name})
            if date is None:
                problems.append(_("the date isn't a date."))
            if amount is None:
                problems.append(_("the opening balance isn't a number."))
            if problems:
                report.errors.append(_("Row %(row)s: %(message)s") % {"row": number, "message": " ".join(problems)})
                continue
            OpeningBalance.objects.update_or_create(
                company=company.head, defaults={"date": date, "amount": amount, "created_by": user}
            )
            report.saved_count += 1
        if report.errors or dry_run:
            transaction.set_rollback(True)
        else:
            report.saved = True
    return report
