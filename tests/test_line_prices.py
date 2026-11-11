"""Receipt and supplier-order lines priced per unit: quantity x price = amount."""

import datetime
from decimal import Decimal

import pytest

from finance_saber.production.forms import ReceiptLineForm
from finance_saber.production.models import Material
from finance_saber.production.order_forms import OrderLineForm

D = Decimal


@pytest.fixture
def sugar(db):
    return Material.objects.create(name="Сахар", unit="кг")


@pytest.mark.parametrize("form_class", [ReceiptLineForm, OrderLineForm])
def test_the_amount_is_quantity_times_price(form_class, sugar):
    data = {"material": sugar.pk, "quantity": "1 250,5", "price": "12 500,25", "amount": "", "status": "ordered"}
    form = form_class(data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["amount"] == D("15631562.63")  # 1250,5 x 12500,25 = 15 631 562,625: half a tiyin up


@pytest.mark.parametrize("form_class", [ReceiptLineForm, OrderLineForm])
def test_a_typed_amount_wins_and_no_price_is_fine(form_class, sugar):
    typed = form_class({"material": sugar.pk, "quantity": "10", "price": "5", "amount": "60", "status": "ordered"})
    assert typed.is_valid() and typed.cleaned_data["amount"] == D(60)
    unpriced = form_class({"material": sugar.pk, "quantity": "10", "price": "", "amount": "", "status": "ordered"})
    assert unpriced.is_valid() and unpriced.cleaned_data["amount"] == 0


def test_a_saved_line_shows_its_price(sugar):
    from finance_saber.cashbook.models import Company
    from finance_saber.production.models import MaterialReceipt

    receipt = MaterialReceipt.objects.create(
        supplier=Company.objects.create(name="ООО Сахар", inn="1"), number="1", date=datetime.date(2026, 11, 1)
    )
    line = receipt.lines.create(material=sugar, quantity=D(8), amount=D(100))
    assert ReceiptLineForm(instance=line).initial["price"] == "12.5"


def test_the_receipt_page_has_the_price_column_and_a_total(client, user):
    from django.urls import reverse

    client.force_login(user)
    content = client.get(reverse("production:material_receipt")).content.decode()
    assert "Цена за единицу" in content and 'data-line-role="price"' in content and "data-lines-total" in content
