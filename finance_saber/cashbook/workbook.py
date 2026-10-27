"""The finance workbook (Situation.xlsx) imported into every cash desk in one go.

Each active desk reads its own sheet: "Касса Бон", "Инкасса & Розница" (see
importer.LAYOUTS) and, for the bank, "Банк". Desks are independent: a desk whose
sheet is missing, or whose rows imported before changed in Excel, is left
unchanged and reported, while the others are imported. Rows and lines imported
before are always skipped, so the same workbook can be uploaded every day.

The page checks the workbook first and imports it on a second click, so the
uploaded file is kept in the system temp folder in between (see keep()).
"""

import datetime
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.db import transaction
from django.db.models import Max

from . import bank, importer
from .models import Account, CashDesk, Counterparty


@dataclass
class DeskResult:
    """What the workbook brings (or brought) to one cash desk."""

    desk: CashDesk
    sheet: str
    error: str = ""  # the sheet couldn't be read; nothing is imported for this desk
    created: int = 0
    skipped: int = 0  # rows / lines imported before
    created_dates: set[datetime.date] = field(default_factory=set)
    conflicts: list[importer.Conflict] = field(default_factory=list)
    updated: list[importer.Conflict] = field(default_factory=list)  # changed rows rewritten from Excel
    adjustments: int = 0
    unsorted: int = 0  # operations filed under "Требует проверки" / "Не распределено"
    # Account name -> (balance here, balance in the sheet), where they differ.
    balance_differences: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)
    new_counterparties: list[str] = field(default_factory=list)  # bank: names of counterparties not in the app
    skipped_unknown: int = 0  # bank: lines left out because their counterparty isn't in the app
    # The latest day in the sheet, and the latest operation already on the desk: when nothing is new,
    # these show whether the workbook simply hasn't got later rows (e.g. it wasn't saved in Excel).
    last_date_in_file: datetime.date | None = None
    last_date_here: datetime.date | None = None

    @property
    def ok(self) -> bool:
        return not self.error and not self.conflicts

    @property
    def period(self) -> tuple[datetime.date, datetime.date] | None:
        return (min(self.created_dates), max(self.created_dates)) if self.created_dates else None

    @property
    def url(self) -> str:
        """The desk's page, showing the days that got new operations."""
        if self.period is None:
            return self.desk.get_absolute_url()
        start, end = self.period
        return f"{self.desk.get_absolute_url()}?date_from={start.isoformat()}&date_to={end.isoformat()}"


@dataclass
class WorkbookImport:
    results: list[DeskResult]
    saved: bool
    add_counterparties: bool = True

    @property
    def created(self) -> int:
        return sum(result.created for result in self.results if not result.error)

    @property
    def new_counterparties(self) -> list[str]:
        return [name for result in self.results for name in result.new_counterparties]

    @property
    def has_errors(self) -> bool:
        return any(not result.ok for result in self.results)

    @property
    def has_new(self) -> bool:
        """Anything to import: new operations, new counterparties, or changed rows to decide on."""
        return bool(self.created or self.new_counterparties or self.conflicts)

    @property
    def conflicts(self) -> int:
        return sum(len(result.conflicts) for result in self.results)


class _Rollback(Exception):
    """Undoes a desk's import when only checking."""


def import_workbook(
    data: bytes, user=None, dry_run: bool = False, add_counterparties: bool = True, update_changed: bool = False
) -> WorkbookImport:
    """Import the workbook's sheets into every active desk that has one.

    ``update_changed`` rewrites operations whose rows changed in Excel since they were imported;
    without it such a desk is reported and left unchanged.
    """
    results = []
    for desk in CashDesk.objects.filter(is_active=True):
        if desk.is_bank:
            result = _import_bank(desk, data, user, dry_run, add_counterparties)
        elif desk.slug in importer.SHEETS_BY_DESK:
            result = _import_cash(desk, data, user, dry_run, update_changed)
        else:
            continue
        result.last_date_here = desk.operations.aggregate(last=Max("date"))["last"]
        results.append(result)
    return WorkbookImport(results, saved=not dry_run, add_counterparties=add_counterparties)


def _import_cash(desk: CashDesk, data: bytes, user, dry_run: bool, update_changed: bool) -> DeskResult:
    result = DeskResult(desk, importer.SHEETS_BY_DESK[desk.slug])
    try:
        upload = importer.upload_sheet(desk, BytesIO(data), user=user, dry_run=dry_run, update_changed=update_changed)
    except importer.SheetError as error:
        result.error = str(error)
        return result
    report = upload.report
    result.last_date_in_file = upload.last_date
    result.created = sum(report.created.values())
    result.skipped = report.skipped_existing
    result.created_dates = report.created_dates | {conflict.sheet.date for conflict in report.updated}
    result.conflicts = report.conflicts
    result.updated = report.updated
    result.adjustments = len(report.adjustments)
    result.unsorted = len(report.unsorted_rows)
    result.balance_differences = {
        str(Account(account).label): values for account, values in upload.balance_differences.items()
    }
    if result.conflicts and not dry_run:  # nothing was saved for this desk
        result.created, result.created_dates = 0, set()
    return result


def _import_bank(desk: CashDesk, data: bytes, user, dry_run: bool, add_counterparties: bool) -> DeskResult:
    result = DeskResult(desk, bank.WORKBOOK_SHEET)
    try:
        lines = bank.read_statement(BytesIO(data), sheet_name=bank.WORKBOOK_SHEET)
    except bank.StatementError as error:
        result.error = str(error)
        return result
    try:
        with transaction.atomic():
            report = bank.record_lines(desk, lines, user=user, create_counterparties=add_counterparties)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    result.last_date_in_file = max((line.posted_at.date() for line in lines), default=None)
    result.created = report.created
    result.skipped = report.duplicates
    result.created_dates = report.created_dates
    result.unsorted = report.unclassified
    result.new_counterparties = [c.name for c in report.new_counterparties] or [
        Counterparty.parse_reference(reference)["name"] or reference for reference in report.unknown_references
    ]
    result.skipped_unknown = report.skipped_unknown
    return result


# --- The uploaded file between "Check" and "Import" -----------------------------

UPLOAD_DIR = Path(tempfile.gettempdir()) / "finance_saber_uploads"
MAX_AGE = 2 * 60 * 60  # seconds; older uploads are deleted
TOKEN = re.compile(r"[0-9a-f]{32}")


def keep(data: bytes) -> str:
    """Store an uploaded workbook until it is imported; returns its token (kept in the user's session)."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - MAX_AGE
    for old in UPLOAD_DIR.glob("*.xlsx"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    (UPLOAD_DIR / f"{token}.xlsx").write_bytes(data)
    return token


def _path(token) -> Path | None:
    return UPLOAD_DIR / f"{token}.xlsx" if isinstance(token, str) and TOKEN.fullmatch(token) else None


def load(token) -> bytes | None:
    """The workbook stored under ``token``, or None if it expired or never existed."""
    path = _path(token)
    if path is None or not path.is_file() or path.stat().st_mtime < time.time() - MAX_AGE:
        return None
    return path.read_bytes()


def discard(token) -> None:
    path = _path(token)
    if path is not None:
        path.unlink(missing_ok=True)
