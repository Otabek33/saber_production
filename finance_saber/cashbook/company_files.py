"""Companies and their bank accounts as an Excel file: the Companies page's download, upload and project file.

One row per bank account: the company's name, INN, region and whether it is ours, and the
account's statement reference ("account/INN/name", as the counterparties list shows
it). A company with no accounts yet has a row with an empty reference. Companies are
matched by INN, accounts by reference; loading is all or nothing.
"""

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from django.db import transaction
from django.http import HttpResponse
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from .exports import Sheet, attachment, xlsx_bytes
from .handbooks import HandbookError, clean, table, texts
from .models import Company, Counterparty, Region

COLUMNS = {
    gettext_noop("Company"): "name",
    gettext_noop("INN"): "inn",
    gettext_noop("Region"): "region",
    gettext_noop("Our company"): "is_own",
    gettext_noop("Statement reference"): "reference",
}
REQUIRED = ("name", "inn")


def companies_sheet() -> Sheet:
    rows = []
    for company in Company.objects.select_related("region").prefetch_related("accounts"):
        start = [
            company.name,
            company.inn,
            company.region.name if company.region else "",
            _("yes") if company.is_own else _("no"),
        ]
        accounts = sorted(company.accounts.all(), key=lambda account: account.name)
        if not accounts:
            rows.append([*start, ""])
        rows.extend([*start, account.reference] for account in accounts)
    headers = [_("Company"), _("INN"), _("Region"), _("Our company"), _("Statement reference")]
    return Sheet(_("Companies"), headers, rows)


# --- The project's file ----------------------------------------------------------------
#
# Kept with the code (cashbook/seed/companies.xlsx), so a new server gets the companies
# from it, after the counterparties file: its rows refer to their statement references.
# ``manage.py export_companies --seed`` writes it again, ``load_companies`` loads it.

SEED_PATH = Path(__file__).resolve().parent / "seed" / "companies.xlsx"


def write_seed_file(path: Path = SEED_PATH) -> Path:
    """Write the project's companies file from the database (in Russian, as the company uses it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with translation.override("ru"):
        path.write_bytes(xlsx_bytes([companies_sheet()]))
    return path


def seed_response() -> HttpResponse | None:
    """The project's companies file as a download, or None if the project has none."""
    if not SEED_PATH.is_file():
        return None
    return attachment(SEED_PATH.read_bytes(), f"{_('Companies')} ({_('project file')}).xlsx")


# --- Upload ------------------------------------------------------------------------------


@dataclass
class CompanyImport:
    sheets: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created: int = 0
    linked: int = 0
    saved: bool = False


def load_companies(file, dry_run: bool = False) -> CompanyImport:
    """Create companies by INN and connect the bank accounts the file names. Nothing is saved on errors or a dry run."""
    try:
        workbook = openpyxl.load_workbook(file, data_only=True, read_only=True)
    except Exception as error:  # not a readable .xlsx
        raise HandbookError(_("The file is not a readable .xlsx workbook.")) from error
    report = CompanyImport()
    try:
        sheet = workbook.worksheets[0]
        report.sheets.append(sheet.title)
        rows = table(sheet, COLUMNS, REQUIRED)
    finally:
        workbook.close()

    yes, no = texts(gettext_noop("yes")) | {"true", "1", "да"}, texts(gettext_noop("no")) | {"false", "0", "нет"}
    regions = {region.name.casefold(): region for region in Region.objects.all()}
    with transaction.atomic():
        for number, row in rows:
            try:
                _load_row(report, row, yes, no, regions)
            except ValueError as error:
                report.errors.append(_("Row %(row)s: %(message)s") % {"row": number, "message": error})
        if report.errors or dry_run:
            transaction.set_rollback(True)
        else:
            report.saved = True
    return report


def _load_row(report: CompanyImport, row: dict, yes: set[str], no: set[str], regions: dict[str, Region]):
    name, inn, reference = clean(row["name"]), clean(row["inn"]), clean(row.get("reference"))
    if not inn:
        raise ValueError(_("the company has no INN."))
    own = clean(row.get("is_own")).casefold()
    if own and own not in yes | no:
        raise ValueError(_("“%(value)s” is not yes or no.") % {"value": clean(row.get("is_own"))})
    region_name = clean(row.get("region"))
    region = regions.get(region_name.casefold()) if region_name else None
    if region_name and region is None:
        raise ValueError(
            _("unknown region “%(name)s”: add it on the Counterparties page first.") % {"name": region_name}
        )
    company, created = Company.objects.get_or_create(
        inn=inn, defaults={"name": name, "is_own": own in yes, "region": region}
    )
    report.created += created
    # An existing company keeps its name; a flag or region the file gives is applied.
    changed = []
    if not created and own and company.is_own != (own in yes):
        company.is_own = own in yes
        changed.append("is_own")
    if not created and region and company.region_id != region.pk:
        company.region = region
        changed.append("region")
    if changed:
        company.save(update_fields=changed)
    if not reference:
        return
    account = Counterparty.objects.filter(reference=reference).first()
    if account is None:
        raise ValueError(_("no bank account with the statement reference “%(reference)s”.") % {"reference": reference})
    if account.company_id != company.pk:
        account.company = company
        account.save(update_fields=["company"])
        report.linked += 1
