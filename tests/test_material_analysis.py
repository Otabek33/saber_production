"""Material analysis: which products a material is enough for, from its stock or any amount, and what runs out first."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from finance_saber.cashbook.models import Company
from finance_saber.production import capacity
from finance_saber.production.models import (
    Material,
    MaterialMove,
    MaterialOrder,
    MaterialOrderLine,
    Product,
    RecipeLine,
)

D = Decimal
PAGE = reverse("production:material_analysis")


def stocked(name, unit, quantity):
    material = Material.objects.create(name=name, unit=unit)
    MaterialMove.objects.create(
        material=material, date=datetime.date(2026, 10, 1), kind=MaterialMove.Kind.OPENING, quantity=D(quantity)
    )
    return material


@pytest.fixture
def recipes(db):
    """Sugar 100 kg; tea uses 0.04 kg a can, juice 0.05; tea's cans (300) run out before its sugar."""
    sugar, cans = stocked("Сахар", "кг", "100"), stocked("Банка", "шт", "300")
    tea = Product.objects.create(name="Чай", volume=D("0.45"))
    juice = Product.objects.create(name="Сок", volume=D("1"))
    old = Product.objects.create(name="Снят с производства", volume=D("1"), is_active=False)
    RecipeLine.objects.create(product=tea, material=sugar, quantity_per_unit=D("0.04"), allowed_loss=D("5"))
    RecipeLine.objects.create(product=tea, material=cans, quantity_per_unit=D("1"))
    RecipeLine.objects.create(product=juice, material=sugar, quantity_per_unit=D("0.05"))
    RecipeLine.objects.create(product=old, material=sugar, quantity_per_unit=D("0.01"))
    return sugar, cans, tea, juice


def test_what_the_stock_is_enough_for_and_what_runs_out_first(recipes):
    sugar, cans, tea, juice = recipes
    rows = {row.product: row for row in capacity.capacity(sugar, D(100))}
    assert set(rows) == {tea, juice}  # an inactive product isn't offered
    assert (rows[tea].from_material, rows[tea].possible, rows[tea].bottleneck) == (2500, 300, cans)
    assert rows[tea].litres == D("1125.0")
    assert (rows[juice].from_material, rows[juice].possible, rows[juice].bottleneck) == (2000, 2000, None)


def test_any_amount_can_be_tried_and_the_allowed_loss_is_a_margin(recipes):
    sugar, _cans, tea, _juice = recipes
    rows = {row.product: row for row in capacity.capacity(sugar, D(10), with_loss=True)}
    assert rows[tea].norm == D("0.042") and rows[tea].from_material == 238  # 10 / 0.042
    assert rows[tea].possible == 238 and not rows[tea].limited_by_other


def test_page_counts_the_stock_or_the_typed_amount_and_goods_in_transit(client, planner, recipes):
    sugar, _cans, tea, _juice = recipes
    order = MaterialOrder.objects.create(
        supplier=Company.objects.create(name="Поставщик", inn="770000003"), order_date=timezone.localdate(),
        expected_date=timezone.localdate() + datetime.timedelta(days=10),
    )  # fmt: skip
    MaterialOrderLine.objects.create(order=order, material=sugar, quantity=D("50"))
    client.force_login(planner)
    assert "form" in client.get(PAGE).context and "rows" not in client.get(PAGE).context
    page = client.get(PAGE, {"material": sugar.pk})
    assert page.context["amount"] == D(100) and not page.context["typed"]
    assert client.get(PAGE, {"material": sugar.pk, "transit": "on"}).context["amount"] == D(150)
    typed = client.get(PAGE, {"material": sugar.pk, "amount": "1 000"})
    assert typed.context["amount"] == D(1000) and typed.context["typed"]
    assert {row.product: row.from_material for row in typed.context["rows"]}[tea] == 25000


def test_materials_page_links_each_material_to_its_analysis(client, planner, recipes):
    sugar, *_rest = recipes
    client.force_login(planner)
    assert f"{PAGE}?material={sugar.pk}" in client.get(reverse("production:materials")).content.decode()


def test_who_sees_the_analysis(client, finance, operator, recipes):
    client.force_login(finance)
    assert client.get(PAGE).status_code == 200
    client.force_login(operator)
    assert client.get(PAGE).status_code == 403
