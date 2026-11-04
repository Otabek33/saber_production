"""Production cost and margin: batches at the price of the deliveries they used, shipments at their batches' cost."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.urls import reverse

from finance_saber.cashbook.models import Company
from finance_saber.production import costing, services
from finance_saber.production.models import (
    Material,
    MaterialMove,
    MaterialReceipt,
    Product,
    ProductionRun,
    ProductShipment,
)

D = Decimal
DAY = {n: datetime.date(2026, 10, n) for n in range(1, 31)}


def receive(supplier, number, date, material, quantity, amount):
    receipt = MaterialReceipt.objects.create(supplier=supplier, number=number, date=date)
    receipt.lines.create(material=material, quantity=D(quantity), amount=D(amount))
    receipt.rebuild_moves()
    return receipt


def produce(product, date, units, material, norm="0.001"):
    line = services.CalculationLine(material=material, quantity_per_unit=D(norm))
    run, _updated = services.save_calculation(date, product, units, None, [line])
    return run


def ship(customer, product, date, units, amount, is_return=False, affects_stock=True):
    shipment = ProductShipment.objects.create(
        date=date, customer=customer, is_return=is_return, affects_stock=affects_stock
    )
    shipment.assign_number()
    shipment.lines.create(product=product, quantity=units, amount=D(amount))
    shipment.rebuild_moves()
    return shipment


@pytest.fixture
def story(db):
    """Sugar: 50 kg opening (no price); A: 100 kg at 10 000/kg on the 1st; B: 200 kg at 12 000/kg on the 3rd.
    Batch 1 (2nd): 120 000 units use 120 kg; batch 2 (4th): 100 000 units use 100 kg."""
    sugar = Material.objects.create(name="Сахар", unit="кг")
    drink = Product.objects.create(name="Чай", volume=D("0.45"))
    a = Company.objects.create(name="ООО Ромашка", inn="1")
    b = Company.objects.create(name="ООО Лютик", inn="2")
    MaterialMove.objects.create(material=sugar, date=DAY[1], kind=MaterialMove.Kind.OPENING, quantity=D(50))
    receive(a, "A-1", DAY[1], sugar, 100, 1_000_000)
    receive(b, "B-1", DAY[3], sugar, 200, 2_400_000)
    first = produce(drink, DAY[2], 120_000, sugar)
    second = produce(drink, DAY[4], 100_000, sugar)
    shop = Company.objects.create(name="Магазин", inn="5")
    filial = Company.objects.create(name="Магазин №2", parent=shop)
    return {"sugar": sugar, "drink": drink, "first": first, "second": second, "shop": shop, "filial": filial}


# --- Batches ----------------------------------------------------------------------------------------


def test_a_batch_costs_the_deliveries_it_used(story):
    costs = costing.batch_costs([story["first"], story["second"]])
    first, second = costs[story["first"].pk], costs[story["second"].pk]
    # 50 kg opening (valued at A's price, an estimate) + 70 kg of A at 10 000.
    assert first.amount == D(1_200_000) and first.per_unit == D(10)
    assert [(part.quantity, part.price.source) for part in first.materials[0].parts] == [
        (D(50), costing.Source.ESTIMATE),
        (D(70), costing.Source.RECEIPT),
    ]
    assert first.estimated_amount == D(500_000) and first.complete and not first.exact
    # 30 kg of A at 10 000 + 70 kg of B at 12 000.
    assert second.amount == D(1_140_000) and second.per_unit == D("11.4")
    assert second.exact


def test_without_any_receipt_price_the_standard_price_stands_in(db):
    salt = Material.objects.create(name="Соль", unit="кг", price=D(3000))
    drink = Product.objects.create(name="Вода", volume=D("0.5"))
    MaterialMove.objects.create(material=salt, date=DAY[1], kind=MaterialMove.Kind.OPENING, quantity=D(10))
    run = produce(drink, DAY[2], 1000, salt, norm="0.002")  # 2 kg
    cost = costing.batch_costs([run])[run.pk]
    assert cost.amount == D(6000) and cost.materials[0].parts[0].price.source == costing.Source.STANDARD


def test_a_material_with_no_price_at_all_makes_the_cost_incomplete(db):
    salt = Material.objects.create(name="Соль", unit="кг")
    drink = Product.objects.create(name="Вода", volume=D("0.5"))
    MaterialMove.objects.create(material=salt, date=DAY[1], kind=MaterialMove.Kind.OPENING, quantity=D(10))
    run = produce(drink, DAY[2], 1000, salt, norm="0.002")
    cost = costing.batch_costs([run])[run.pk]
    assert not cost.complete and cost.missing == [salt] and cost.per_unit is None


def test_a_history_batch_is_valued_by_what_its_norms_wrote_off(story):
    run = ProductionRun.objects.create(
        date=DAY[6], product=story["drink"], quantity=10_000, status=ProductionRun.Status.POSTED, affects_stock=False
    )
    run.lines.create(material=story["sugar"], quantity_per_unit=D("0.001"))
    run.rebuild_moves()  # no stock movements: imported history
    cost = costing.batch_costs([run])[run.pk]
    assert cost.amount == D(120_000)  # 10 kg at B's price, the latest receipt up to the 6th
    assert cost.estimated_amount == cost.amount


# --- Shipments --------------------------------------------------------------------------------------


def test_a_shipment_costs_its_batches_oldest_first(story):
    shipment = ship(story["shop"], story["drink"], DAY[6], 150_000, 3_000_000)
    [item] = costing.shipment_margins([shipment])
    # 120 000 from batch 1 at 10 + 30 000 from batch 2 at 11,4.
    assert item.cost == D(1_542_000) and item.margin == D(1_458_000)
    assert item.estimated_amount == D(1_200_000)  # batch 1 has an estimated part


def test_history_shipments_and_returns_take_the_cost_per_unit_at_their_date(story):
    history = ship(story["shop"], story["drink"], DAY[7], 1000, 20_000, affects_stock=False)
    returned = ship(story["shop"], story["drink"], DAY[8], 100, 2_000, is_return=True)
    early = ship(story["shop"], story["drink"], DAY[1], 10, 200, affects_stock=False)  # before any batch
    items = {item.shipment.pk: item for item in costing.shipment_margins([history, returned, early])}
    assert items[history.pk].cost == D(11_400)  # batch 2's 11,4: the latest up to the 7th
    assert (items[returned.pk].revenue, items[returned.pk].cost) == (D(-2000), D(-1140))
    assert items[early.pk].cost == D(100)  # the first batch after it, at 10


def test_margin_by_customer_adds_filials_to_their_head_company(story):
    ship(story["shop"], story["drink"], DAY[6], 1000, 20_000)
    ship(story["filial"], story["drink"], DAY[7], 1000, 25_000)
    items = costing.shipment_margins(ProductShipment.objects.all())
    [row] = costing.group_margins(items, "customer")
    assert (row.label, row.units, row.revenue, row.cost) == ("Магазин", 2000, D(45_000), D(20_000))
    assert row.percent == D("55.6")


# --- Pages ------------------------------------------------------------------------------------------


def test_the_cost_page_shows_cost_per_unit_and_the_change(client, finance, story):
    client.force_login(finance)
    response = client.get(reverse("production:costs"), {"month": "2026-10"})
    [row] = response.context["rows"]
    assert (row.units, row.amount) == (220_000, D(2_340_000))
    assert response.context["selected"] == row
    [material] = response.context["selected_materials"]
    assert material["material"] == story["sugar"] and material["share"] == D(100)
    assert "Себестоимость" in response.content.decode()


def test_the_margin_page_and_its_excel(client, finance, story):
    ship(story["shop"], story["drink"], DAY[6], 150_000, 3_000_000)
    client.force_login(finance)
    params = {"from": "2026-10-01", "to": "2026-10-31", "by": "product"}
    response = client.get(reverse("production:margin"), params)
    assert response.context["totals"].margin == D(1_458_000)
    export = client.get(reverse("production:margin"), params | {"export": "xlsx"})
    sheet = openpyxl.load_workbook(BytesIO(export.content)).active
    assert [cell.value for cell in sheet[2]][:5] == ["Чай", 150_000, 3_000_000, 1_542_000, 1_458_000]


def test_the_batch_page_shows_its_cost_to_those_who_may_see_costs(client, finance, technologist, story):
    url = reverse("production:batch", args=[story["second"].pk])
    client.force_login(finance)
    assert "Себестоимость партии" in client.get(url).content.decode()
    client.force_login(technologist)
    assert "Себестоимость партии" not in client.get(url).content.decode()


def test_costs_are_for_finance_and_the_director_only(client, technologist, user):
    client.force_login(technologist)
    assert client.get(reverse("production:costs")).status_code == 403
    assert client.get(reverse("production:margin")).status_code == 403
    client.force_login(user)  # the director
    assert client.get(reverse("production:margin")).status_code == 200


def test_a_material_takes_a_standard_price(client, user):
    salt = Material.objects.create(name="Соль", unit="кг")
    client.force_login(user)
    data = {"name": "Соль", "unit": "кг", "kind": "raw", "min_stock": "0", "price": "12 500,50", "is_active": "on"}
    assert client.post(reverse("production:material_edit", args=[salt.pk]), data).status_code == 302
    salt.refresh_from_db()
    assert salt.price == D("12500.50")
