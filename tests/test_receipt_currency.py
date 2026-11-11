"""Receipts in another currency (amounts as invoiced and in UZS), and the stock's worth in USD."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from finance_saber.cashbook.models import Company, ExchangeRate
from finance_saber.production import costing, services
from finance_saber.production.models import Material, MaterialReceipt, Product

D = Decimal


@pytest.fixture
def setup(db):
    sugar = Material.objects.create(name="Сахар", unit="кг")
    supplier = Company.objects.create(name="Sugar Ltd", inn="1")
    ExchangeRate.objects.create(date=datetime.date(2026, 11, 1), rate=D(12800))
    return {"sugar": sugar, "supplier": supplier}


def post_receipt(client, setup, currency="", rate="", quantity="100", price="5", amount="", number="INV-1"):
    return client.post(reverse("production:material_receipt"), {
        "supplier": setup["supplier"].pk, "number": number, "date": "2026-11-05", "currency": currency,
        "rate": rate, "note": "",
        "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-material": setup["sugar"].pk, "lines-0-quantity": quantity, "lines-0-price": price,
        "lines-0-amount": amount,
    })  # fmt: skip


def test_a_usd_receipt_keeps_the_invoice_and_its_uzs_amount(client, technologist, setup):
    client.force_login(technologist)
    assert post_receipt(client, setup, currency="USD", rate="12 750").status_code == 302
    receipt = MaterialReceipt.objects.get()
    line = receipt.lines.get()
    assert (receipt.currency, receipt.rate) == ("USD", D(12750))
    assert (line.currency_amount, line.currency_price, line.amount) == (D(500), D(5), D(6_375_000))
    page = client.get(reverse("production:receipt", args=[receipt.pk])).content.decode()
    flat = page.replace(" ", " ").replace(" ", " ")
    assert "USD" in page and "6 375 000" in flat and "500,00" in flat


def test_the_usd_rate_of_the_day_is_taken_when_left_empty(client, technologist, setup):
    client.force_login(technologist)
    post_receipt(client, setup, currency="USD", rate="")
    assert MaterialReceipt.objects.get().rate == D(12800)


def test_another_currency_needs_its_rate(client, technologist, setup):
    client.force_login(technologist)
    response = post_receipt(client, setup, currency="EUR", rate="")
    assert "rate" in response.context["form"].errors and not MaterialReceipt.objects.exists()


def test_a_uzs_receipt_is_as_before(client, technologist, setup):
    client.force_login(technologist)
    post_receipt(client, setup, price="12 000")
    receipt = MaterialReceipt.objects.get()
    line = receipt.lines.get()
    assert (receipt.currency, receipt.rate, line.amount, line.currency_amount) == (
        "UZS",
        None,
        D(1_200_000),
        D(1_200_000),
    )


def test_the_stock_is_worth_its_deliveries_in_usd(client, technologist, setup):
    client.force_login(technologist)
    post_receipt(client, setup, currency="USD", rate="12 750", number="INV-1")  # 100 kg at $5
    post_receipt(client, setup, price="25 600", quantity="50", number="INV-2")  # 50 kg at 25 600 UZS = $2 at 12 800
    drink = Product.objects.create(name="Чай", volume=D("0.45"))
    services.save_calculation(datetime.date(2026, 11, 6), drink, 20_000, None, [
        services.CalculationLine(setup["sugar"], D("0.001")),
    ])  # 20 kg used: from the USD delivery (the oldest)  # fmt: skip
    value = costing.stock_value_usd(datetime.date(2026, 11, 10))
    assert value.rate == D(12800)
    assert value.by_material[setup["sugar"].pk] == D(80) * 5 + D(50) * 2  # $400 + $100
    assert value.total == D(500) and value.estimated == 0


def test_only_those_who_see_costs_see_the_stock_value(client, technologist, finance, setup):
    client.force_login(technologist)
    post_receipt(client, setup, currency="USD", rate="12 750")
    assert client.get(reverse("production:materials")).context["stock_value"] is None
    client.force_login(finance)
    response = client.get(reverse("production:materials"))
    assert response.context["stock_value"].total == D(500)
    assert "Стоимость сырья на складе" in response.content.decode()


def test_the_form_offers_the_usd_rate_of_the_day(client, technologist, setup):
    client.force_login(technologist)
    form = client.get(reverse("production:material_receipt")).context["form"]
    assert form["rate"].value() == D(12800)


def test_a_receipt_line_takes_its_good_until_date(client, technologist, setup):
    client.force_login(technologist)
    url = reverse("production:material_receipt")
    data = {
        "supplier": setup["supplier"].pk, "number": "INV-5", "date": "2026-11-05", "currency": "", "rate": "",
        "note": "", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1",
        "lines-MAX_NUM_FORMS": "1000", "lines-0-material": setup["sugar"].pk, "lines-0-quantity": "10",
        "lines-0-price": "", "lines-0-amount": "", "lines-0-best_before": "2026-11-01",
    }  # fmt: skip
    response = client.post(url, data)
    assert "best_before" in response.context["lines"].forms[0].errors and not MaterialReceipt.objects.exists()
    assert client.post(url, data | {"lines-0-best_before": "2027-05-01"}).status_code == 302
    assert MaterialReceipt.objects.get().lines.get().best_before == datetime.date(2027, 5, 1)
