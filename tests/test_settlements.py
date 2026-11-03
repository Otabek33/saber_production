"""Settlements with partners: balances from shipments, returns and money; the акт сверки; opening balances."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from finance_saber.cashbook.forms import OperationForm
from finance_saber.cashbook.models import (
    Account,
    CashDesk,
    Category,
    Company,
    Counterparty,
    ExchangeRate,
    Kind,
    Operation,
)
from finance_saber.production.models import Product, ProductShipment, ProductShipmentLine
from finance_saber.settlements import ledger
from finance_saber.settlements.models import OpeningBalance

D = Decimal
DAY = {n: datetime.date(2026, 10, n) for n in range(1, 31)}
BALANCES = reverse("settlements:balances")


@pytest.fixture
def partner(db):
    """A chain with a shop (a filial), its bank account and a product."""
    head = Company.objects.create(name="ООО Ромашка", inn="770000001")
    shop = Company.objects.create(name="Ромашка магазин №1", parent=head)
    account = Counterparty.objects.create(reference="2020/770000001/ROMASHKA", name="ROMASHKA", company=head)
    product = Product.objects.create(name="Чай", volume=D("0.45"))
    return head, shop, account, product


def ship(customer, product, amount, date, is_return=False, quantity=10):
    shipment = ProductShipment.objects.create(date=date, customer=customer, is_return=is_return)
    shipment.assign_number()
    ProductShipmentLine.objects.create(shipment=shipment, product=product, quantity=quantity, amount=D(amount))
    shipment.rebuild_moves()
    return shipment


def money(kind, amount, date, desk="bank", account=Account.BANK, **link):
    return Operation.objects.create(
        desk=CashDesk.objects.get(slug=desk), kind=kind, date=date, category=Category.objects.first(),
        account=account, amount=D(amount), **link,
    )  # fmt: skip


@pytest.fixture
def activity(partner):
    head, shop, account, product = partner
    ship(head, product, "1000000", DAY[2])
    ship(shop, product, "500000", DAY[5])
    ship(shop, product, "100000", DAY[6], is_return=True)
    money(Kind.INCOME, "700000", DAY[8], counterparty=account)  # paid from the bank
    money(Kind.INCOME, "200000", DAY[9], desk="inkassa", account=Account.CASH_UZS, partner=shop)  # cash at the shop
    money(Kind.EXPENSE, "50000", DAY[10], counterparty=account)  # a refund
    return partner


def test_balance_counts_the_filials_shipments_returns_and_money(activity):
    head, *_rest = activity
    [row] = ledger.balances()
    assert row.partner == head
    assert (row.shipped, row.returned, row.received, row.paid) == (D(1500000), D(100000), D(900000), D(50000))
    assert row.balance == D(550000)  # 1 500 000 − 100 000 − 900 000 + 50 000
    assert row.last_date == DAY[10]
    assert ledger.balances(on_date=DAY[5])[0].balance == D(1500000)


def test_statement_lists_every_entry_with_the_balance_after_it(activity):
    head, shop, *_rest = activity
    result = ledger.statement(head, DAY[5], DAY[30])
    assert result.opening == D(1000000)  # the shipment of the 2nd, before the period
    assert [(entry.kind, entry.debit, entry.credit, entry.balance) for entry in result.entries] == [
        ("shipment", D(500000), 0, D(1500000)),
        ("return", 0, D(100000), D(1400000)),
        ("received", 0, D(700000), D(700000)),
        ("received", 0, D(200000), D(500000)),
        ("paid", D(50000), 0, D(550000)),
    ]
    assert result.entries[0].company == shop
    assert (result.debit, result.credit, result.closing) == (D(550000), D(1000000), D(550000))


def test_opening_balance_replaces_what_came_before_it(activity):
    head, *_rest = activity
    OpeningBalance.objects.create(company=head, date=DAY[5], amount=D("-300000"))  # we owed it on the 5th
    [row] = ledger.balances()
    assert row.opening == D(-300000)
    assert row.shipped == D(500000)  # the 2nd isn't counted again
    assert row.balance == D(-300000 + 500000 - 100000 - 900000 + 50000)
    result = ledger.statement(head)
    assert (result.date_from, result.opening) == (DAY[5], D(-300000))
    assert result.opening_credit == D(300000)


def test_usd_cash_counts_in_uzs_at_its_rate_or_the_days_rate(partner):
    head, *_rest = partner
    OpeningBalance.objects.create(company=head, date=DAY[1], amount=D(0))
    ExchangeRate.objects.create(date=DAY[1], rate=D("12000"))
    money(Kind.INCOME, "100", DAY[3], desk="bon", account=Account.CASH_USD, partner=head)
    money(Kind.INCOME, "10", DAY[4], desk="bon", account=Account.CASH_USD, partner=head, rate=D("12500"))
    assert ledger.balances()[0].received == D(1200000) + D(125000)


def test_money_alone_does_not_make_a_partner(partner):
    _head, _shop, account, _product = partner
    money(Kind.EXPENSE, "1000", DAY[3], counterparty=account)  # e.g. a fee to a company we don't ship to
    assert ledger.balances() == []


def test_cancelled_documents_and_money_are_not_counted(activity):
    head, *_rest = activity
    ProductShipment.objects.filter(customer=head).update(is_cancelled=True)
    Operation.objects.update(is_cancelled=True)
    [row] = ledger.balances()
    assert row.balance == D(500000 - 100000)


# --- Pages ---------------------------------------------------------------------------------


def test_balances_page_and_its_excel(client, finance, activity):
    head, *_rest = activity
    client.force_login(finance)
    page = client.get(BALANCES)
    assert [row.partner for row in page.context["rows"]] == [head]
    assert page.context["totals"]["owed_to_us"] == D(550000)
    assert client.get(BALANCES, {"show": "creditors"}).context["rows"] == []
    response = client.get(BALANCES, {"export": "xlsx"})
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    assert [row[0] for row in sheet.iter_rows(min_row=2, values_only=True)] == ["ООО Ромашка"]


def test_statement_page_and_the_akt_sverki_file(client, finance, activity):
    head, shop, *_rest = activity
    client.force_login(finance)
    url = reverse("settlements:statement", args=[head.pk])
    assert client.get(reverse("settlements:statement", args=[shop.pk]))["Location"] == url  # a filial: its head
    page = client.get(url, {"from": "2026-10-05", "to": "2026-10-30"})
    assert page.context["statement"].closing == D(550000)
    assert "Ромашка магазин №1" in page.content.decode()
    response = client.get(url, {"from": "2026-10-05", "to": "2026-10-30", "export": "xlsx"})
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    texts = [cell for row in sheet.iter_rows(values_only=True) for cell in row if isinstance(cell, str)]
    assert any("АКТ СВЕРКИ" in text for text in texts)
    assert any("05.10.2026" in text and "30.10.2026" in text for text in texts)
    assert any("550 000,00" in text for text in texts)  # who owes whom, in words


def test_opening_balance_is_entered_on_the_statement_page(client, finance, superuser, activity):
    head, *_rest = activity
    url = reverse("settlements:statement", args=[head.pk])
    client.force_login(finance)
    client.post(url, {"date": "2026-10-01", "amount": "-1 250 000,50", "note": "из 1С"})
    opening = OpeningBalance.objects.get(company=head)
    assert (opening.date, opening.amount, opening.created_by) == (DAY[1], D("-1250000.50"), finance)
    client.post(url, {"remove": "1"})  # only the superuser removes
    assert OpeningBalance.objects.exists()
    client.force_login(superuser)
    client.post(url, {"remove": "1"})
    assert not OpeningBalance.objects.exists()


def test_opening_balances_from_excel(client, finance, activity):
    head, shop, *_rest = activity
    client.force_login(finance)
    response = client.get(reverse("settlements:openings_export"))
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    assert list(sheet.iter_rows(min_row=2, values_only=True))[0][:2] == ("770000001", "ООО Ромашка")

    book = openpyxl.Workbook()
    book.active.append(["ИНН", "Компания", "Дата", "Сальдо начальное"])
    book.active.append(["770000001", "ООО Ромашка", "01.10.2026", 250000])
    buffer = BytesIO()
    book.save(buffer)
    file = SimpleUploadedFile("openings.xlsx", buffer.getvalue())
    client.post(reverse("settlements:openings_import"), {"file": file})
    assert OpeningBalance.objects.get(company=head).amount == D(250000)

    book.active.append(["", "Нет такой", "01.10.2026", 1])
    buffer = BytesIO()
    book.save(buffer)
    client.post(reverse("settlements:openings_import"), {"file": SimpleUploadedFile("o.xlsx", buffer.getvalue())})
    assert OpeningBalance.objects.count() == 1  # all or nothing


def test_cash_desk_lines_name_their_partner_and_the_bank_does_not(partner):
    head, *_rest = partner
    cash = OperationForm(desk=CashDesk.objects.get(slug="inkassa"))
    assert head in cash.fields["partner"].queryset
    assert "partner" not in OperationForm(desk=CashDesk.objects.get(slug="bank")).fields


def test_who_sees_settlements(client, technologist, operator, activity):
    client.force_login(technologist)
    assert client.get(BALANCES).status_code == 403
    client.force_login(operator)
    assert client.get(BALANCES).status_code == 200
    head, *_rest = activity
    client.post(reverse("settlements:statement", args=[head.pk]), {"date": "2026-10-01", "amount": "1"})
    assert not OpeningBalance.objects.exists()  # operator views only


def test_without_an_opening_balance_counting_starts_at_the_first_shipment(activity):
    _head, _shop, account, _product = activity
    money(Kind.INCOME, "9000000", DAY[1], counterparty=account)  # before the first shipment (the 2nd)
    assert ledger.counting_start() == DAY[2]
    assert ledger.balances()[0].received == D(900000)
