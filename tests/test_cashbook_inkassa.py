"""The "Инкасса & Розница" desk: UZS cash only, its own handbook, and its sheet import."""

import datetime
from decimal import Decimal

import openpyxl
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from finance_saber.cashbook.forms import OperationForm
from finance_saber.cashbook.importer import CashSheetImporter, inkassa_category, read_sheet
from finance_saber.cashbook.ledger import balances
from finance_saber.cashbook.models import Account, CashDesk, Category, Kind, Operation

D = Decimal
DAY = datetime.datetime(2026, 1, 5)


@pytest.fixture
def inkassa(db):
    return CashDesk.objects.get(slug="inkassa")  # added by migration 0005


@pytest.fixture
def retail(db):
    return Category.objects.get(article__name="Приход розница", name="Приход розница")


@pytest.mark.parametrize(
    ("note", "category"),
    [
        ("стоянка", "Стоянка"),
        ("арава стоянка", "Стоянка"),
        ("клик", "Клик"),
        ("покупка брака", "Брак"),
        ("склад обед", "Питание"),
        ("карз", "Карз и долг"),
        ("заправка картриджа", "Картридж и печать"),
        ("газ заправка", "Топливо"),
        ("вулканизация", "Машина"),
        ("банкомат", "Прочие"),
    ],
)
def test_dist_expense_category_comes_from_the_note(note, category):
    assert inkassa_category("Дист расход", note) == category


def test_other_articles_are_their_own_category():
    assert inkassa_category("Инкассо", "Инкассо") == "Инкассо"


# --- Desk rules ----------------------------------------------------------------


def test_desk_only_accepts_its_accounts_and_articles(inkassa, retail):
    operation = Operation(desk=inkassa, kind=Kind.INCOME, category=retail, account=Account.CARD, amount=D("5"))
    operation.date = DAY.date()
    with pytest.raises(ValidationError) as error:
        operation.full_clean()
    assert "account" in error.value.message_dict

    salary = Category.objects.get(article__name="Взаиморасчеты с персоналом", name="ЗП")  # a Касса БОН article
    operation = Operation(desk=inkassa, kind=Kind.EXPENSE, category=salary, account=Account.CASH_UZS, amount=D("5"))
    operation.date = DAY.date()
    with pytest.raises(ValidationError) as error:
        operation.full_clean()
    assert "category" in error.value.message_dict


def test_single_account_desk_form_has_no_transfer_or_account_choice(inkassa):
    form = OperationForm(desk=inkassa)
    assert [value for value, _ in form.fields["kind"].choices] == [Kind.INCOME, Kind.EXPENSE]
    assert not form.show_account
    assert not form.show_rate
    articles = {category.article.name for category in form.fields["category"].queryset}
    assert "Дист расход" in articles
    assert "Взаиморасчеты с персоналом" not in articles


def test_inkassa_page_shows_one_column_per_group(client, user, inkassa, retail):
    client.force_login(user)
    op = Operation(desk=inkassa, kind=Kind.INCOME, category=retail, account=Account.CASH_UZS, amount=D("19915000"))
    op.date = DAY.date()
    op.save()
    response = client.get(reverse("cashbook:operations", kwargs={"desk": "inkassa"}) + "?date_from=2026-01-01")
    assert response.status_code == 200
    assert [account["value"] for account in response.context["accounts"]] == [Account.CASH_UZS]
    content = response.content.decode()
    assert 'colspan="1"' in content
    assert "Карта" not in content


def test_add_operation_on_inkassa(client, user, inkassa):
    client.force_login(user)
    parking = Category.objects.get(article__name="Дист расход", name="Стоянка")
    response = client.post(
        reverse("cashbook:operations", kwargs={"desk": "inkassa"}),
        {"kind": "expense", "date": "2026-10-20", "category": parking.pk, "account": "cash_uzs", "amount": "15000"},
    )
    assert response.status_code == 302
    assert inkassa.operations.get().category == parking


# --- Sheet import --------------------------------------------------------------


def make_inkassa_workbook(path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Инкасса & Розница"
    for letter, text in {
        "B": "Категория",
        "C": "Организация",
        "D": "Дата",
        "E": "Примичание",
        "F": "UZS",
        "G": "UZS",
    }.items():
        sheet[f"{letter}4"] = text
    sheet["H5"] = 1000000  # opening balance
    rows = [
        {"D": DAY, "B": "Приход розница", "E": "Приход розница", "F": 500000},
        {"D": DAY, "B": "Дист расход", "E": "стоянка", "G": 20000},
        {"D": DAY, "B": "Дист расход", "E": "карз", "G": -30000},  # money returned
        {"D": DAY, "B": "Инкассо", "E": "Инкассо", "G": 1200000},
    ]
    balance = 1000000
    for number, values in enumerate(rows, start=6):
        for letter, value in values.items():
            sheet[f"{letter}{number}"] = value
        balance += values.get("F", 0) - values.get("G", 0)
        sheet[f"H{number}"] = balance
    workbook.save(path)
    return path


def test_import_inkassa_sheet(inkassa, tmp_path):
    parsed = read_sheet(make_inkassa_workbook(tmp_path / "Situation.xlsx"), "Инкасса & Розница")
    assert parsed.opening == {Account.CASH_UZS: D("1000000")}
    assert [row.category for row in parsed.rows] == ["Приход розница", "Стоянка", "Карз и долг", "Инкассо"]

    report = CashSheetImporter(inkassa, "Situation.xlsx/Инкасса & Розница").import_rows(parsed.rows, parsed.opening)

    assert not report.adjustments  # the rows add up to the sheet's balance
    assert balances(inkassa.operations.all())[Account.CASH_UZS] == parsed.closing[Account.CASH_UZS] == D("310000")
    opening = inkassa.operations.get(source="Situation.xlsx/Инкасса & Розница/opening/opening-cash_uzs")
    assert (opening.kind, opening.amount, opening.category.name) == (Kind.INCOME, D("1000000"), "Начальный остаток")
    parking = inkassa.operations.get(note="стоянка")
    assert (parking.category.article.name, parking.category.name) == ("Дист расход", "Стоянка")
