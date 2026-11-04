"""The Production dashboard: output and drafts, materials at risk, finished goods' days of cover, who sees what."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from finance_saber.cashbook.models import Company
from finance_saber.core.dashboard import Period
from finance_saber.production import overview, services
from finance_saber.production.models import (
    Material,
    MaterialMove,
    MaterialOrder,
    MaterialOrderLine,
    Product,
    ProductionRun,
    ProductShipment,
    ProductShipmentLine,
    RecipeLine,
)

D = Decimal
TODAY = timezone.localdate()
PAGE = reverse("production:index")


def opening(material, quantity, date=TODAY - datetime.timedelta(days=40)):
    MaterialMove.objects.create(material=material, date=date, kind=MaterialMove.Kind.OPENING, quantity=D(quantity))


@pytest.fixture
def plant(db):
    """Tea uses 0.1 kg sugar a can; 30 days of making 100 cans a day used 300 kg (10 kg a day)."""
    sugar = Material.objects.create(name="Сахар", unit="кг")
    cans = Material.objects.create(name="Банка", unit="шт", min_stock=D("5000"))
    salt = Material.objects.create(name="Соль", unit="кг")  # not used: never at risk
    opening(sugar, "400")
    opening(cans, "20000")
    opening(salt, "10")
    tea = Product.objects.create(name="Чай", volume=D("0.45"))
    RecipeLine.objects.create(product=tea, material=sugar, quantity_per_unit=D("0.1"))
    RecipeLine.objects.create(product=tea, material=cans, quantity_per_unit=D("1"))
    for day in range(1, 31):
        services.save_day(TODAY - datetime.timedelta(days=day), {tea: 100})
    return sugar, cans, salt, tea


def test_output_counts_posted_days_and_lists_drafts(plant):
    *_materials, tea = plant
    services.save_day(TODAY, {tea: 50}, post=False)  # a draft: not counted
    period = Period(TODAY - datetime.timedelta(days=6), TODAY, "week")
    result = overview.output(period)
    assert (result["units"], result["working_days"], result["per_day"]) == (600, 6, 100)
    assert result["litres"] == D(270)
    assert result["draft_count"] == 1 and result["drafts"][0].quantity == 50


def test_materials_running_out_soon_with_their_days_of_cover_and_transit(plant):
    sugar, cans, salt, _tea = plant
    order = MaterialOrder.objects.create(
        supplier=Company.objects.create(name="Поставщик", inn="770000003"),
        order_date=TODAY, expected_date=TODAY + datetime.timedelta(days=5),
    )  # fmt: skip
    MaterialOrderLine.objects.create(order=order, material=sugar, quantity=D("1000"))
    result = overview.materials(Period(TODAY, TODAY), with_transit=True)
    rows = {row["material"]: row for row in result["rows"]}
    assert set(rows) == {sugar}  # 100 kg left at 10 kg a day; cans: 17 000 (above the minimum, 170 days)
    assert (rows[sugar]["days"], rows[sugar]["level"]) == (10, "low")
    assert rows[sugar]["transit"]["quantity"] == D(1000)
    assert salt not in rows


def test_a_material_below_zero_or_its_minimum_is_at_risk(plant):
    sugar, cans, _salt, tea = plant
    services.save_day(TODAY, {tea: 1600}, post=True)  # sugar −60 kg, cans 15 400
    cans.min_stock = D("16000")
    cans.save()
    result = overview.materials(Period(TODAY, TODAY), with_transit=False)
    levels = {row["material"]: (row["level"], row["below_min"]) for row in result["rows"]}
    assert levels == {sugar: ("negative", False), cans: ("low", True)}
    assert result["rows"][0]["material"] == sugar and result["negative"] == 1


def test_finished_goods_last_as_long_as_the_recent_shipping_allows(plant):
    *_materials, tea = plant
    shop = Company.objects.create(name="Магазин", inn="770000004")
    shipment = ProductShipment.objects.create(date=TODAY - datetime.timedelta(days=1), customer=shop)
    ProductShipmentLine.objects.create(shipment=shipment, product=tea, quantity=600)
    shipment.rebuild_moves()  # 3 000 made, 600 shipped in 30 days: 20 a day; 2 400 left
    [row] = overview.goods(Period(TODAY, TODAY))["rows"]
    assert (row["stock"], row["days"]) == (2400, 120)


def test_chart_is_by_day_or_by_month(plant):
    daily = overview.chart(Period(TODAY - datetime.timedelta(days=6), TODAY), with_shipments=True)
    assert len(daily["labels"]) == 7 and daily["produced"][:6] == [100] * 6 and "shipped" in daily
    monthly = overview.chart(Period(TODAY - datetime.timedelta(days=200), TODAY), with_shipments=False)
    assert monthly["monthly"] and "shipped" not in monthly


def test_page_and_who_sees_which_block(client, planner, finance, operator, plant):
    client.force_login(planner)
    context = client.get(PAGE, {"period": "month"}).context
    for block in ("output", "chart", "materials", "transit", "goods", "receipts"):
        assert block in context, block
    client.force_login(finance)  # views production, has no drafts to post or orders to enter
    assert client.get(PAGE).status_code == 200
    client.force_login(operator)
    assert client.get(PAGE).status_code == 403
    assert ProductionRun.objects.count() == 30  # nothing changed by looking
