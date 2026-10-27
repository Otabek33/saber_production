"""Importing a "Касса" sheet of the finance workbook."""

import datetime
from decimal import Decimal
from io import BytesIO, StringIO

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command

from finance_saber.cashbook.importer import (
    CashSheetImporter,
    SheetError,
    SheetRow,
    plan_row,
    read_sheet,
    upload_sheet,
)
from finance_saber.cashbook.ledger import balances
from finance_saber.cashbook.models import Account, Category, Kind, Operation

D = Decimal
DAY = datetime.datetime(2026, 10, 20)

HEADERS = {
    **{"A": "Залог", "C": "Категория", "D": "Организация", "E": "Дата", "F": "Курс", "G": "Примичание"},
    **{
        "H": "UZS",
        "I": "USD",
        "J": "Карта",
        "L": "UZS",
        "M": "USD",
        "N": "Карта",
        "P": "UZS",
        "Q": "USD",
        "R": "Карта",
    },
}


def row(**kwargs):
    return SheetRow(number=10, date=DAY.date(), **kwargs)


# --- Turning one sheet row into operations -------------------------------------


def test_single_amount_is_one_operation():
    [op] = plan_row(row(expense={Account.CARD: D("1850000")}))
    assert (op.kind, op.account, op.amount) == (Kind.EXPENSE, Account.CARD, D("1850000"))


def test_exchange_becomes_a_transfer_with_the_amount_received():
    [op] = plan_row(row(income={Account.CASH_USD: D("9000")}, expense={Account.CASH_UZS: D("109800000")}))
    assert (op.kind, op.account, op.amount, op.to_account, op.to_amount) == (
        Kind.TRANSFER, Account.CASH_UZS, D("109800000"), Account.CASH_USD, D("9000"),
    )  # fmt: skip


def test_equal_cash_to_card_becomes_a_transfer():
    [op] = plan_row(row(income={Account.CARD: D("1000000")}, expense={Account.CASH_UZS: D("1000000")}))
    assert (op.kind, op.account, op.to_account) == (Kind.TRANSFER, Account.CASH_UZS, Account.CARD)


def test_unequal_same_currency_pair_is_split():
    ops = plan_row(row(income={Account.CARD: D("5060000")}, expense={Account.CASH_UZS: D("8793000")}))
    assert sorted((op.kind, op.amount) for op in ops) == [(Kind.EXPENSE, D("8793000")), (Kind.INCOME, D("5060000"))]


def test_several_expenses_become_several_operations():
    ops = plan_row(row(expense={Account.CASH_UZS: D("9000000"), Account.CASH_USD: D("300"), Account.CARD: D("5")}))
    assert len(ops) == 3
    assert {op.kind for op in ops} == {Kind.EXPENSE}


def test_negative_expense_is_income():
    [op] = plan_row(row(expense={Account.CASH_UZS: D("-1850000")}))
    assert (op.kind, op.amount) == (Kind.INCOME, D("1850000"))


# --- Reading a workbook --------------------------------------------------------


def make_workbook(path, rows, title="Касса Бон"):
    """A workbook laid out like Situation.xlsx / "Касса Бон"; rows are {column letter: value}."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = title
    for letter, text in HEADERS.items():
        sheet[f"{letter}4"] = text
    for number, values in enumerate(rows, start=6):
        for letter, value in values.items():
            sheet[f"{letter}{number}"] = value
    workbook.save(path)
    return path


SHEET_ROWS = [
    {"B": "Продажа БОН", "C": "Продажа БОН", "E": DAY, "G": "пополнение карт", "J": 120000000},
    {"B": "Командировка", "C": "Командировка", "E": DAY, "G": "Жамшид ака к/р", "N": 1850000.0000001},
    {"B": "Обмена валюты", "C": "Обмена валюты", "E": DAY, "F": 12200, "I": 9000, "L": 109800000},
    {"B": "Прочие расходы", "C": None, "E": None, "G": "без даты и категории", "L": 100},
    # The sheet's balance is typed in here: 1 000 000 cash UZS more than the rows add up to.
    {"B": None, "C": None, "E": DAY, "P": -109800100 + 1000000, "Q": 9000, "R": 118150000},
    {"G": "черновой расчёт под таблицей", "H": 5},  # below the last dated row: ignored
]


@pytest.fixture
def workbook(tmp_path):
    return make_workbook(tmp_path / "Situation.xlsx", SHEET_ROWS)


def test_read_sheet(workbook):
    sheet = read_sheet(workbook, "Касса Бон")
    rows, closing = sheet.rows, sheet.closing
    assert [r.number for r in rows] == [6, 7, 8, 9, 10]
    assert rows[1].expense == {Account.CARD: D("1850000.00")}  # float noise rounded to cents
    assert rows[3].date == DAY.date() and rows[3].date_filled
    assert closing == {Account.CASH_UZS: D("-108800100"), Account.CASH_USD: D("9000"), Account.CARD: D("118150000")}


def test_read_sheet_rejects_another_layout(tmp_path):
    path = tmp_path / "other.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "Касса Бон"
    workbook.save(path)
    with pytest.raises(SheetError, match="Unexpected sheet layout"):
        read_sheet(path, "Касса Бон")


# --- Importing -----------------------------------------------------------------


@pytest.fixture
def desk(db):
    from finance_saber.cashbook.models import CashDesk

    return CashDesk.objects.get(slug="bon")


def test_import_creates_operations_and_reconciles(desk, workbook):
    sheet = read_sheet(workbook, "Касса Бон")
    rows, closing = sheet.rows, sheet.closing
    report = CashSheetImporter(desk, "Situation.xlsx/Касса Бон").import_rows(rows)

    assert report.created == {Kind.INCOME: 2, Kind.EXPENSE: 2, Kind.TRANSFER: 1}  # incl. one correction
    exchange = Operation.objects.get(kind=Kind.TRANSFER)
    assert (exchange.to_amount, exchange.rate, exchange.category.name) == (D("9000"), D("12200"), "Обмена валюты")
    unsorted = Operation.objects.get(note="без даты и категории")
    assert unsorted.category.name == "Требует проверки"
    assert unsorted.category.article.name == "Прочие расходы"
    correction = Operation.objects.get(source="Situation.xlsx/Касса Бон/10/adj-cash_uzs")
    assert (correction.kind, correction.amount) == (Kind.INCOME, D("1000000"))
    assert report.adjustments == [(10, Account.CASH_UZS, D("1000000"))]
    imported = balances(desk.operations.all())
    assert {account: imported[account] for account in closing} == closing


def test_import_can_be_rerun_without_duplicates(desk, workbook):
    rows = read_sheet(workbook, "Касса Бон").rows
    CashSheetImporter(desk, "Situation.xlsx/Касса Бон").import_rows(rows)
    count = Operation.objects.count()
    report = CashSheetImporter(desk, "Situation.xlsx/Касса Бон").import_rows(rows)
    assert Operation.objects.count() == count
    assert report.skipped_existing == count
    assert sum(report.created.values()) == 0


def test_command_dry_run_saves_nothing(desk, workbook):
    out = StringIO()
    call_command("import_cash_xlsx", str(workbook), "--dry-run", stdout=out)
    assert "Dry run" in out.getvalue()
    assert "MISMATCH" not in out.getvalue()
    assert not Operation.objects.exists()
    assert not Category.objects.filter(name="Требует проверки").exists()


def test_command_imports(desk, workbook):
    out = StringIO()
    call_command("import_cash_xlsx", str(workbook), stdout=out)
    assert Operation.objects.count() == 5
    assert out.getvalue().count(" OK") == 3


# --- Uploading the workbook every day ------------------------------------------

NEXT_DAY = datetime.datetime(2026, 10, 21)
DAYS_ROWS = SHEET_ROWS[:5]  # without the loose calculation below the table
TAXI = {"B": "Командировка", "C": "Командировка", "E": NEXT_DAY, "G": "такси", "L": 50000, "P": -108800100 - 50000}


def workbook_file(rows, name="Situation.xlsx", title="Касса Бон"):
    """The workbook as an uploaded file."""
    return SimpleUploadedFile(name, make_workbook(BytesIO(), rows, title).getvalue())


def changed_trip():
    """DAYS_ROWS with the business trip changed from 1 850 000 to 1 900 000 after it was uploaded."""
    changed = [dict(values) for values in DAYS_ROWS]
    changed[1]["N"] = 1900000
    return changed


def test_upload_adds_only_new_rows(desk):
    first = upload_sheet(desk, workbook_file(DAYS_ROWS))
    assert first.saved
    assert sum(first.report.created.values()) == 5

    # The next day's copy, saved under another name: only the new row is added.
    second = upload_sheet(desk, workbook_file([*DAYS_ROWS, TAXI], name="Situation (2).xlsx"))
    assert second.saved
    assert second.report.created == {Kind.EXPENSE: 1}
    assert second.report.created_dates == {NEXT_DAY.date()}
    assert second.report.skipped_existing == 5
    assert second.report.adjustments == []  # the correction was added by the first upload
    assert second.balance_differences == {}
    assert desk.operations.count() == 6


def test_upload_with_a_changed_row_saves_nothing(desk):
    upload_sheet(desk, workbook_file(DAYS_ROWS))
    result = upload_sheet(desk, workbook_file([*changed_trip(), TAXI]))
    assert not result.saved
    [conflict] = result.report.conflicts
    assert conflict.row == 7
    assert (conflict.operation.amount, conflict.sheet.amount) == (D("1850000"), D("1900000"))
    assert desk.operations.count() == 5  # not even the new row


def test_upload_notices_a_row_inserted_in_the_middle(desk):
    upload_sheet(desk, workbook_file(DAYS_ROWS))
    taxi_same_day = {"B": "Командировка", "C": "Командировка", "E": DAY, "G": "такси", "L": 50000}
    result = upload_sheet(desk, workbook_file([DAYS_ROWS[0], taxi_same_day, *DAYS_ROWS[1:]]))
    assert not result.saved
    assert result.report.conflicts
    assert desk.operations.count() == 5


def test_upload_dry_run_saves_nothing(desk):
    result = upload_sheet(desk, workbook_file(DAYS_ROWS), dry_run=True)
    assert not result.saved
    assert sum(result.report.created.values()) == 5
    assert not Operation.objects.exists()


def test_command_refuses_changed_rows(desk, workbook, tmp_path):
    call_command("import_cash_xlsx", str(workbook), stdout=StringIO())
    path = make_workbook(tmp_path / "copy.xlsx", changed_trip())
    with pytest.raises(CommandError, match="changed in Excel"):
        call_command("import_cash_xlsx", str(path), stdout=StringIO())
    assert Operation.objects.count() == 5


def test_upload_finds_the_sheet_whatever_its_case_and_spaces(desk):
    result = upload_sheet(desk, workbook_file(DAYS_ROWS, title=" КАССА  бон"))
    assert result.saved
    assert desk.operations.count() == 5
