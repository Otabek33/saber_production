"""Companies, their filials and bank accounts as an Excel file: the Companies page's download, upload and project file.

The file is SmartUp's "Юридическое лицо" export, as SmartUp gives it or as the Companies
page's download writes it (the same columns, plus ours: head company INN and name,
sales region, our company, statement reference). One row per company; a company
with several bank accounts has a row per account.

* A company is found by its SmartUp ID ("ИД"), else by its INN among head companies
  not yet linked to another SmartUp record; otherwise it is created. A company found
  by INN keeps its name; everything else the file gives is updated.
* "Владелец" Y marks a filial; its head company is the row whose ID is its "ИД
  владельца". A filial whose head isn't loaded yet keeps that ID and is attached when
  its head arrives. A second company with an INN already taken goes under the first.
* The bank account (МФО, расчетный счет) is added to the company's requisites, and
  the bank-statement counterparty with that account number is connected to it.

A file without the "ИД" column is the older download: name, INN, region (the sales
region), our company and statement reference. Loading is all or nothing.
"""

from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path

import openpyxl
from django.db import transaction
from django.http import HttpResponse
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from .exports import Sheet, attachment, xlsx_bytes
from .handbooks import HandbookError, clean, table, texts
from .models import Company, CompanyBankAccount, Counterparty, Region

# SmartUp's columns -> Company fields, in SmartUp's order. Headers are matched with
# spaces collapsed, so "ИД  владельца" (two spaces, as SmartUp writes it) matches.
SMARTUP_FIELDS = {
    "ИД": "smartup_id",
    "Код": "code",
    "ИД владельца": "smartup_owner_id",
    "Владелец": "is_filial",
    "Штрих-код": "barcode",
    "ИНН(*)": "inn",
    "Название(*)": "name",
    "Альтернативное название": "short_name",
    "ОКЭД": "oked",
    "НДС": "vat_number",
    "МФО": "mfo",
    "Расчетный счет(основной)": "account_number",
    "Банк(основной)": "bank",
    "Код валюты (пустое поле означает базовую валюту)": "currency",
    "Страна": "country",
    "Регион": "area",
    "Город/поселок": "city",
    "Улица/район": "district",
    "Адрес": "address",
    "Почтовый адрес": "postal_address",
    "Ориентир": "landmark",
    "Почтовый индекс": "postal_code",
    "Группа": "group",
    "Категория": "category",
    "Тип": "kind",
    "Телефон": "phone",
    "Email": "email",
    "GPS координаты": "gps",
    "Рабочая зона": "work_zones",
}
# Our own columns, after SmartUp's (translated in the file, found in any language).
OUR_FIELDS = {
    gettext_noop("Head company INN"): "parent_inn",
    gettext_noop("Head company"): "parent_name",  # for reading only
    gettext_noop("Sales region"): "region",
    gettext_noop("Our company"): "is_own",
    gettext_noop("Statement reference"): "reference",
}
# The older download's columns; its "Region" is the sales region.
LEGACY_FIELDS = {
    gettext_noop("Company"): "name",
    gettext_noop("INN"): "inn",
    gettext_noop("Region"): "region",
}
DETAILS = [  # the Company fields a SmartUp file sets as they are
    "code", "barcode", "short_name", "oked", "vat_number", "country", "area", "city", "district", "address",
    "postal_address", "landmark", "postal_code", "group", "category", "kind", "phone", "email", "gps", "work_zones",
]  # fmt: skip


def companies_sheet() -> Sheet:
    """Every company, each head company followed by its filials."""
    companies = Company.objects.select_related("parent", "region").prefetch_related("bank_accounts", "accounts")
    children: dict[int | None, list[Company]] = {}
    for company in sorted(companies, key=lambda company: (company.name.casefold(), company.pk)):
        children.setdefault(company.parent_id, []).append(company)
    rows, stack = [], list(reversed(children.get(None, [])))
    while stack:
        company = stack.pop()
        rows.extend(_company_rows(company))
        stack.extend(reversed(children.get(company.pk, [])))
    headers = [*SMARTUP_FIELDS, *(_(header) for header in OUR_FIELDS)]
    return Sheet(_("Companies"), headers, rows)


def _company_rows(company: Company) -> list[list]:
    """A row per bank account (a requisite, a statement counterparty, or both), at least one."""
    parent = company.parent
    owner_id = company.smartup_owner_id or (parent.smartup_id if parent else "")
    values = {name: getattr(company, name) for name in DETAILS} | {
        "smartup_id": company.smartup_id, "smartup_owner_id": owner_id, "inn": company.inn, "name": company.name,
        "is_filial": "Y" if parent or owner_id else "N",
    }  # fmt: skip
    ours = [
        parent.inn if parent else "",
        parent.name if parent else "",
        company.region.name if company.region else "",
        _("yes") if company.is_own else _("no"),
    ]
    requisites = list(company.bank_accounts.all())
    references = sorted(account.reference for account in company.accounts.all())
    rows = []
    for requisite, reference in zip_longest(requisites, references) if requisites or references else [(None, None)]:
        bank = {
            "mfo": requisite.mfo, "account_number": requisite.account_number, "bank": requisite.bank,
            "currency": requisite.currency,
        } if requisite else {}  # fmt: skip
        rows.append([*((values | bank).get(name, "") for name in SMARTUP_FIELDS.values()), *ours, reference or ""])
    return rows


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
    updated: int = 0
    filials: int = 0  # companies put under a head company
    waiting: set[str] = field(default_factory=set)  # head companies (SmartUp ID or INN) not loaded yet
    requisites: int = 0  # bank accounts added to companies' requisites
    linked: int = 0  # bank-statement counterparties connected
    saved: bool = False


def load_companies(file, dry_run: bool = False) -> CompanyImport:
    """Create or update companies and filials, add their bank accounts. Nothing is saved on errors or a dry run."""
    try:
        workbook = openpyxl.load_workbook(file, data_only=True, read_only=True)
    except Exception as error:  # not a readable .xlsx
        raise HandbookError(_("The file is not a readable .xlsx workbook.")) from error
    report = CompanyImport()
    try:
        sheet = workbook.worksheets[0]
        report.sheets.append(sheet.title)
        header = {clean(value).casefold() for value in next(sheet.iter_rows(max_row=1, values_only=True), ())}
        smartup = "ид" in header
        if smartup:
            columns = {header.replace("(*)", ""): name for header, name in SMARTUP_FIELDS.items()} | SMARTUP_FIELDS
            rows = table(sheet, columns | OUR_FIELDS, ("smartup_id", "name"))
        else:
            rows = table(sheet, LEGACY_FIELDS | OUR_FIELDS, ("name", "inn"))
    finally:
        workbook.close()

    loader = _Loader(report, smartup)
    rows = [(number, {name: clean(value) for name, value in row.items()}) for number, row in rows]
    rows = [(number, row) for number, row in rows if any(row.values())]
    with transaction.atomic():
        # Head companies first, so a filial sharing its head's INN never takes the head's place.
        for number, row in sorted(rows, key=lambda item: loader.is_filial(item[1])):
            try:
                loader.load(row)
            except ValueError as error:
                report.errors.append((number, _("Row %(row)s: %(message)s") % {"row": number, "message": error}))
        report.errors = [message for _number, message in sorted(report.errors)]
        if not report.errors:
            loader.attach_filials()
        if report.errors or dry_run:
            transaction.set_rollback(True)
        else:
            report.saved = True
    return report


class _Loader:
    def __init__(self, report: CompanyImport, smartup: bool):
        self.report, self.smartup = report, smartup
        self.yes = texts(gettext_noop("yes")) | {"y", "да", "true", "1"}
        self.no = texts(gettext_noop("no")) | {"n", "нет", "false", "0"}
        self.regions = {region.name.casefold(): region for region in Region.objects.all()}
        self.parent_inns: dict[int, str] = {}  # company pk -> its head company's INN, from our file
        self.seen: set[int] = set()  # companies loaded from an earlier row (the row of another bank account)

    def is_filial(self, row: dict) -> bool:
        """Y, or a head company INN in our file; an N row with an owner but no INN of its own (a shop) is one too."""
        if row.get("is_filial", "").casefold() in self.yes or row.get("parent_inn"):
            return True
        return bool(row.get("smartup_owner_id")) and not row.get("inn")

    def load(self, row: dict):
        name, inn, smartup_id = row.get("name", ""), row.get("inn", ""), row.get("smartup_id", "")
        if not name:
            raise ValueError(_("the company has no name."))
        if not self.smartup and not inn:
            raise ValueError(_("the company has no INN."))
        for flag in ("is_own", "is_filial"):
            if row.get(flag) and row[flag].casefold() not in self.yes | self.no:
                raise ValueError(_("“%(value)s” is not yes or no.") % {"value": row[flag]})
        region = self.region(row.get("region", ""))

        is_filial = self.is_filial(row)
        company, created = self.find(smartup_id, inn, name, is_filial)
        if company.pk not in self.seen:
            if created or (smartup_id and company.smartup_id == smartup_id):
                company.name = name  # a company from SmartUp is named as SmartUp names it
            company.inn = inn or company.inn
            if self.smartup:
                for detail in DETAILS:
                    if detail in row:
                        setattr(company, detail, row[detail])
                company.smartup_id = smartup_id or company.smartup_id
                # A company of its own (N, with its INN) stays a head company, whatever owner SmartUp gives it.
                company.smartup_owner_id = row.get("smartup_owner_id", "") if is_filial else ""
            if row.get("is_own"):
                company.is_own = row["is_own"].casefold() in self.yes
            if region:
                company.region = region
            if company.parent_id is None and company.inn:
                # A second company with an INN already taken goes under the first one (a filial's
                # owner, found by its ID once every row is loaded, then becomes its head).
                company.parent = Company.objects.heads().filter(inn=company.inn).exclude(pk=company.pk).first()
            company.save()
            self.seen.add(company.pk)
            self.report.created += created
            self.report.updated += not created
            if row.get("parent_inn"):
                self.parent_inns[company.pk] = row["parent_inn"]
        self.add_bank_account(company, row)
        self.connect_reference(company, row.get("reference", ""))

    def find(self, smartup_id: str, inn: str, name: str, is_filial: bool) -> tuple[Company, bool]:
        if smartup_id and (company := Company.objects.filter(smartup_id=smartup_id).first()):
            return company, False
        if inn and not (self.smartup and is_filial):
            # A head company by its INN, unless it already is another SmartUp record (then this one is new).
            # A SmartUp filial is never taken for the company with its INN: a shop isn't the legal entity.
            company = Company.objects.heads().filter(inn=inn).first()
            if company and not (smartup_id and company.smartup_id):
                return company, False
        elif not smartup_id and (company := Company.objects.filter(smartup_id="", name=name).first()):
            return company, False
        return Company(), True

    def region(self, name: str) -> Region | None:
        if not name:
            return None
        if (region := self.regions.get(name.casefold())) is None:
            raise ValueError(_("unknown region “%(name)s”: add it on the Counterparties page first.") % {"name": name})
        return region

    def add_bank_account(self, company: Company, row: dict):
        if not (number := row.get("account_number", "")):
            return
        _account, created = CompanyBankAccount.objects.update_or_create(
            company=company,
            mfo=row.get("mfo", ""),
            account_number=number,
            defaults={"bank": row.get("bank", ""), "currency": row.get("currency", "")},
        )
        self.report.requisites += created
        # The bank-statement counterparty with this account number is the same account.
        digits = "".join(filter(str.isdigit, number))
        self.report.linked += Counterparty.objects.filter(account_number=digits, company=None).update(company=company)

    def connect_reference(self, company: Company, reference: str):
        if not reference:
            return
        account = Counterparty.objects.filter(reference=reference).first()
        if account is None:
            raise ValueError(
                _("no bank account with the statement reference “%(reference)s”.") % {"reference": reference}
            )
        if account.company_id != company.pk:
            account.company = company
            account.save(update_fields=["company"])
            self.report.linked += 1

    def attach_filials(self):
        """Put each filial under its head company: its owner by SmartUp ID, or the head by INN (our file).

        Companies from earlier files still waiting for their owner are tried too.
        """
        by_smartup_id = dict(Company.objects.exclude(smartup_id="").values_list("smartup_id", "pk"))
        owners = dict(Company.objects.exclude(smartup_owner_id="").values_list("pk", "smartup_owner_id"))
        wanted = {pk: by_smartup_id.get(owner_id) for pk, owner_id in owners.items()}
        for pk, inn in self.parent_inns.items():
            if not wanted.get(pk):
                heads = Company.objects.heads().filter(inn=inn).exclude(pk=pk)
                wanted[pk] = heads.values_list("pk", flat=True).first()
        chain = dict(Company.objects.exclude(parent=None).values_list("pk", "parent_id"))
        for pk, parent_pk in wanted.items():
            if parent_pk is None:
                self.report.waiting.add(owners.get(pk) or self.parent_inns[pk])
            elif chain.get(pk) != parent_pk:
                if _makes_a_loop(pk, parent_pk, chain):
                    name = Company.objects.get(pk=pk).name
                    self.report.errors.append(_("“%(name)s” would be a filial of its own filial.") % {"name": name})
                    continue
                Company.objects.filter(pk=pk).update(parent_id=parent_pk)
                chain[pk] = parent_pk
                self.report.filials += 1


def _makes_a_loop(pk: int, parent_pk: int, chain: dict[int, int]) -> bool:
    seen, current = {pk}, parent_pk
    while current is not None:
        if current in seen:
            return True
        seen.add(current)
        current = chain.get(current)
    return False
