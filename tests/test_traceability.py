"""Batch traceability: which deliveries went into which batch, first in, first out."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.urls import reverse

from finance_saber.cashbook.models import Company
from finance_saber.production import services, traceability
from finance_saber.production.models import Material, MaterialMove, MaterialReceipt, Product

D = Decimal
DAY = {n: datetime.date(2026, 10, n) for n in range(1, 31)}


def receive(supplier, number, date, material, quantity):
    receipt = MaterialReceipt.objects.create(supplier=supplier, number=number, date=date)
    receipt.lines.create(material=material, quantity=D(quantity))
    receipt.rebuild_moves()
    return receipt


def produce(product, date, units, material, norm="0.001"):
    """A day's production; the norm is per unit, so 120 000 units × 0.001 = 120 kg."""
    line = services.CalculationLine(material=material, quantity_per_unit=D(norm))
    run, _updated = services.save_calculation(date, product, units, None, [line])
    return run


@pytest.fixture
def story(db):
    """Sugar: 50 opening; A brings 100 on the 1st, B 200 on the 3rd; batches use 120 (2nd) and 100 (4th)."""
    sugar = Material.objects.create(name="Сахар", unit="кг")
    drink = Product.objects.create(name="Чай", volume=D("0.45"))
    a = Company.objects.create(name="ООО Ромашка", inn="1")
    b = Company.objects.create(name="ООО Лютик", inn="2")
    MaterialMove.objects.create(material=sugar, date=DAY[1], kind=MaterialMove.Kind.OPENING, quantity=D(50))
    receipt_a = receive(a, "A-1", DAY[1], sugar, 100)
    receipt_b = receive(b, "B-1", DAY[3], sugar, 200)
    first = produce(drink, DAY[2], 120_000, sugar)
    second = produce(drink, DAY[4], 100_000, sugar)
    write_off = MaterialMove.objects.create(
        material=sugar, date=DAY[5], kind=MaterialMove.Kind.WRITE_OFF, quantity=D(-20)
    )
    return {
        "sugar": sugar, "drink": drink, "a": a, "b": b, "receipt_a": receipt_a, "receipt_b": receipt_b,
        "first": first, "second": second, "write_off": write_off,
    }  # fmt: skip


def sources(run):
    [material] = traceability.batch_materials(run)
    return [(piece.lot.label if piece.lot else None, piece.quantity) for piece in material.pieces]


# --- The matching ---------------------------------------------------------------------------


def test_a_batch_uses_the_oldest_stock_first(story):
    assert sources(story["first"]) == [("Начальный остаток", D(50)), ("Поступление № A-1", D(70))]
    assert sources(story["second"]) == [("Поступление № A-1", D(30)), ("Поступление № B-1", D(70))]


def test_a_delivery_shows_every_batch_it_went_into_and_what_is_left(story):
    [use_a] = traceability.lot_uses(story["receipt_a"])
    assert [(run, quantity) for run, quantity in use_a.batches] == [(story["first"], D(70)), (story["second"], D(30))]
    assert (use_a.used, use_a.remaining) == (D(100), D(0))
    [use_b] = traceability.lot_uses(story["receipt_b"])
    assert [(run, quantity) for run, quantity in use_b.batches] == [(story["second"], D(70))]
    assert [(move, quantity) for move, quantity in use_b.other] == [(story["write_off"], D(20))]
    assert use_b.remaining == D(110)


def test_a_delivery_counts_before_use_on_the_same_day(story):
    late = Company.objects.create(name="ООО Утро", inn="3")
    drink, sugar = story["drink"], story["sugar"]
    receive(late, "C-1", DAY[10], sugar, 500)
    run = produce(drink, DAY[10], 10_000, sugar)  # 10 kg; 110 of B is older, so B first
    assert sources(run) == [("Поступление № B-1", D(10))]


def test_a_back_dated_delivery_takes_its_place_in_line(story):
    early = Company.objects.create(name="ООО Ранняя", inn="4")
    receive(early, "Z-0", DAY[1] - datetime.timedelta(days=5), story["sugar"], 30)
    # Entered late but dated earliest: the first batch now uses it before the opening stock... of the 1st.
    assert sources(story["first"])[0] == ("Поступление № Z-0", D(30))


def test_using_more_than_recorded_stock_is_shown(story):
    run = produce(story["drink"], DAY[20], 1_000_000, story["sugar"])  # 1000 kg, 110 in stock
    assert sources(run) == [("Поступление № B-1", D(110)), (None, D(890))]


def test_a_cancelled_delivery_is_no_longer_a_source(client, story, technologist):
    client.force_login(technologist)
    client.post(reverse("production:receipt_cancel", args=[story["receipt_a"].pk]))
    # B came on the 3rd, after this batch (the 2nd): without A, 70 kg are not covered by any delivery.
    assert sources(story["first"]) == [("Начальный остаток", D(50)), (None, D(70))]
    assert sources(story["second"]) == [("Поступление № B-1", D(100))]


def test_the_batch_keeps_what_it_was_made_of_after_the_recipe_changes(story):
    produce(story["drink"], DAY[25], 1_000, story["sugar"], norm="0.005")  # a later day, new norm
    [material] = traceability.batch_materials(story["first"])
    assert (material.line.quantity_per_unit, material.line.consumed) == (D("0.001"), D(120))


def test_batch_codes(story):
    run = story["first"]
    code = traceability.batch_code(run)
    assert code == f"261002-{run.product_id:03d}"
    assert traceability.parse_batch_code(code) == (DAY[2], run.product_id)
    assert traceability.parse_batch_code("not a code") is None
    assert traceability.parse_batch_code("261399-001") is None  # no 99th day


# --- The pages ----------------------------------------------------------------------------------


def listed(client, **params):
    response = client.get(reverse("production:batches"), params)
    assert response.status_code == 200
    return [run.code for run in response.context["page_obj"]]


def test_batches_list_and_its_filters(client, story, technologist):
    client.force_login(technologist)
    first, second = traceability.batch_code(story["first"]), traceability.batch_code(story["second"])
    assert listed(client) == [second, first]
    assert listed(client, code=first) == [first]
    assert listed(client, supplier=story["b"].pk) == [second]  # forward: whose materials came from B
    assert listed(client, receipt=story["receipt_a"].pk) == [second, first]
    assert listed(client, **{"from": DAY[3].isoformat()}) == [second]


def test_batch_page_shows_suppliers_and_links_their_receipts(client, story, planner):
    client.force_login(planner)
    page = client.get(reverse("production:batch", args=[story["second"].pk])).content.decode()
    assert traceability.batch_code(story["second"]) in page
    assert "ООО Ромашка" in page and "ООО Лютик" in page
    assert reverse("production:receipt", args=[story["receipt_b"].pk]) in page


def test_receipt_page_shows_where_its_materials_went(client, story, technologist):
    client.force_login(technologist)
    page = client.get(reverse("production:receipt", args=[story["receipt_b"].pk])).content.decode()
    assert "Куда ушло это сырьё" in page
    assert traceability.batch_code(story["second"]) in page
    assert "Списание" in page  # the write-off took 20 of it


def test_daily_page_links_the_days_batches(client, story, technologist):
    client.force_login(technologist)
    page = client.get(reverse("production:daily"), {"date": DAY[2].isoformat()}).content.decode()
    assert reverse("production:batch", args=[story["first"].pk]) in page


def test_register_has_a_row_per_batch_material_and_delivery(client, story, finance):
    client.force_login(finance)  # finance views production, and may download the register
    response = client.get(reverse("production:batches_export"))
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    header = [cell.value for cell in sheet[1]]
    rows = [dict(zip(header, row, strict=True)) for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert [(r["Партия"], r["Расход"], r["Из этой поставки"], r["Поставщик"]) for r in rows] == [
        (traceability.batch_code(story["second"]), 100, 30, "ООО Ромашка"),
        (traceability.batch_code(story["second"]), 100, 70, "ООО Лютик"),
        (traceability.batch_code(story["first"]), 120, 50, None),  # opening stock: no supplier
        (traceability.batch_code(story["first"]), 120, 70, "ООО Ромашка"),
    ]


def test_operator_cannot_see_batches(client, story, operator):
    client.force_login(operator)
    assert client.get(reverse("production:batches")).status_code == 403
    assert client.get(reverse("production:batch", args=[story["first"].pk])).status_code == 403
