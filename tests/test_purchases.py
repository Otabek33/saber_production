"""Purchases: material receipts with amounts and bills are what we owe suppliers; payments settle them."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.urls import reverse

from finance_saber.cashbook.models import Account, CashDesk, Category, Company, Counterparty, Kind, Operation
from finance_saber.production.models import Material, MaterialReceipt, MaterialReceiptLine
from finance_saber.purchases.models import Bill
from finance_saber.settlements import ledger

D = Decimal
DAY = {n: datetime.date(2026, 10, n) for n in range(1, 31)}


@pytest.fixture
def supplier(db):
    company = Company.objects.create(name="ООО Сахарный завод", inn="770000005")
    account = Counterparty.objects.create(reference="2020/770000005/SUGAR", name="SUGAR", company=company)
    return company, account


@pytest.fixture
def sugar(db):
    return Material.objects.create(name="Сахар", unit="кг")


def receive(supplier, material, quantity, amount, date=DAY[2], number="СФ-1"):
    receipt = MaterialReceipt.objects.create(date=date, supplier=supplier, number=number)
    MaterialReceiptLine.objects.create(receipt=receipt, material=material, quantity=D(quantity), amount=D(amount))
    receipt.rebuild_moves()
    return receipt


def pay(account, amount, date):
    return Operation.objects.create(
        desk=CashDesk.objects.get(slug="bank"), kind=Kind.EXPENSE, date=date, category=Category.objects.first(),
        account=Account.BANK, amount=D(amount), counterparty=account,
    )  # fmt: skip


@pytest.fixture
def purchases(supplier, sugar):
    company, account = supplier
    receive(company, sugar, "1000", "12000000", DAY[2], "СФ-1")
    receive(company, sugar, "500", "0", DAY[3], "СФ-2")  # not priced yet: not counted
    Bill.objects.create(date=DAY[4], supplier=company, number="ТР-7", description="Доставка", amount=D("800000"))
    pay(account, "10000000", DAY[6])
    return supplier


def test_receipts_with_amounts_and_bills_are_owed_until_paid(purchases):
    company, _account = purchases
    [row] = ledger.balances()
    assert row.partner == company
    assert (row.purchased, row.paid) == (D(12800000), D(10000000))
    assert row.balance == D(-2800000)  # we owe the supplier


def test_supplier_statement_lists_receipts_bills_and_payments(purchases):
    company, _account = purchases
    result = ledger.statement(company)
    assert [(entry.kind, entry.debit, entry.credit) for entry in result.entries] == [
        ("receipt", 0, D(12000000)), ("bill", 0, D(800000)), ("paid", D(10000000), 0),
    ]  # fmt: skip
    assert result.closing == D(-2800000)
    assert result.entries[1].text == "Счёт № ТР-7: Доставка"


def test_a_partner_who_buys_and_sells_has_one_balance(purchases, client, finance):
    """A supplier we also ship to: the акт сверки nets both."""
    from finance_saber.production.models import Product, ProductShipment, ProductShipmentLine

    company, _account = purchases
    shipment = ProductShipment.objects.create(date=DAY[5], customer=company)
    product = Product.objects.create(name="Чай", volume=D("0.45"))
    ProductShipmentLine.objects.create(shipment=shipment, product=product, quantity=100, amount=D("700000"))
    [row] = ledger.balances()
    assert row.balance == D(-2800000 + 700000)


def test_vendors_page_shows_suppliers_only(client, finance, purchases):
    company, _account = purchases
    Company.objects.create(name="Только покупатель", inn="770000099")
    client.force_login(finance)
    page = client.get(reverse("purchases:vendors"))
    assert [row.partner for row in page.context["rows"]] == [company]
    assert page.context["owed"] == D(2800000)
    sheet = openpyxl.load_workbook(BytesIO(client.get(reverse("purchases:vendors"), {"export": "xlsx"}).content)).active
    assert list(sheet.iter_rows(min_row=2, values_only=True))[0][-1] == -2800000


def test_bills_journal_lists_receipts_and_bills_and_warns_about_unpriced_receipts(client, finance, purchases):
    client.force_login(finance)
    page = client.get(reverse("purchases:bills"))
    assert [(row["kind"], row["number"]) for row in page.context["page_obj"]] == [
        ("bill", "ТР-7"), ("receipt", "СФ-2"), ("receipt", "СФ-1"),
    ]  # fmt: skip
    assert page.context["total"] == D(12800000)
    assert page.context["unpriced"] == 1
    assert [row["kind"] for row in client.get(reverse("purchases:bills"), {"kind": "bills"}).context["page_obj"]] == [
        "bill"
    ]


def test_a_bill_is_entered_once_and_cancelled_not_deleted(client, finance, supplier):
    company, _account = supplier
    client.force_login(finance)
    data = {
        "supplier": company.pk,
        "date": "2026-10-10",
        "number": "тр-8",
        "description": "Ремонт",
        "amount": "1 500 000",
    }
    assert client.post(reverse("purchases:bill_new"), data).status_code == 302
    bill = Bill.objects.get()
    assert (bill.number, bill.amount, bill.created_by) == ("ТР-8", D(1500000), finance)
    assert client.post(reverse("purchases:bill_new"), data).status_code == 200  # the same number again
    client.post(reverse("purchases:bill_cancel", args=[bill.pk]))
    bill.refresh_from_db()
    assert bill.is_cancelled and ledger.balances() == []


def test_payments_made_lists_money_paid_to_suppliers(client, finance, purchases):
    client.force_login(finance)
    page = client.get(reverse("purchases:payments"))
    assert [row["amount"] for row in page.context["page_obj"]] == [D(10000000)]
    assert page.context["total"] == D(10000000)


def test_receipt_form_takes_amounts(client, technologist, supplier, sugar):
    company, _account = supplier
    client.force_login(technologist)
    client.post(reverse("production:material_receipt"), {
        "supplier": company.pk, "number": "СФ-9", "date": "2026-10-10", "note": "",
        "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-material": sugar.pk, "lines-0-quantity": "100", "lines-0-amount": "1 200 000",
        "lines-1-material": "", "lines-1-quantity": "", "lines-1-amount": "",
    })  # fmt: skip
    receipt = MaterialReceipt.objects.get()
    assert receipt.total == D(1200000) and receipt.lines.get().price == D(12000)


def test_who_works_with_purchases(client, technologist, operator, finance):
    for user, status in ((technologist, 403), (operator, 403), (finance, 200)):
        client.force_login(user)
        assert client.get(reverse("purchases:bills")).status_code == status
