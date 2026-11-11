"""Goods in transit: supplier orders with currency and arrival, their status, and receiving them into stock."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.urls import reverse
from django.utils import timezone

from finance_saber.cashbook.models import Company, ExchangeRate
from finance_saber.production.models import (
    Material,
    MaterialOrder,
    MaterialOrderLine,
    MaterialReceipt,
    MaterialReceiptLine,
    material_stock,
    materials_in_transit,
)

D = Decimal
TODAY = timezone.localdate()
NEW = reverse("production:order_new")


@pytest.fixture
def supplier(db):
    return Company.objects.create(name="Döhler GmbH", inn="770000007")


@pytest.fixture
def sugar(db):
    return Material.objects.create(name="Сахар", unit="кг")


@pytest.fixture
def flavour(db):
    return Material.objects.create(name="Ароматизатор Мята", unit="кг")


def order_data(supplier, lines, **extra):
    data = {
        "supplier": supplier.pk, "number": "", "order_date": TODAY.isoformat(), "expected_date": "",
        "lead_days": "", "currency": "USD", "status": "ordered", "note": "",
        "lines-TOTAL_FORMS": str(len(lines)), "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1",
        "lines-MAX_NUM_FORMS": "1000", **extra,
    }  # fmt: skip
    for index, (material, quantity, amount) in enumerate(lines):
        data |= {
            f"lines-{index}-material": material.pk if material else "",
            f"lines-{index}-quantity": quantity,
            f"lines-{index}-amount": amount,
            f"lines-{index}-status": "ordered",  # the select's default, on an empty row too
            f"lines-{index}-expected_date": "",
        }
    return data


def receive(client, order, quantities, number="INV-77", rate="12 150"):
    """Post the order page's receive form: {line: quantity that arrived}."""
    data = {"date": TODAY.isoformat(), "number": number, "rate": rate}
    data |= {f"line_{line.pk}": quantity for line, quantity in quantities.items()}
    return client.post(reverse("production:order_receive", args=[order.pk]), data)


def make_order(supplier, lines, expected, status=MaterialOrder.Status.ORDERED, currency="USD"):
    order = MaterialOrder.objects.create(
        supplier=supplier, order_date=TODAY - datetime.timedelta(days=30), expected_date=expected, status=status,
        currency=currency,
    )  # fmt: skip
    order.assign_number()
    for material, quantity, amount in lines:
        MaterialOrderLine.objects.create(order=order, material=material, quantity=D(quantity), amount=D(amount))
    return order


# --- Entering an order --------------------------------------------------------------------------


def test_planner_orders_materials_with_a_delivery_time(client, planner, supplier, sugar, flavour):
    client.force_login(planner)
    response = client.post(NEW, order_data(supplier, [(sugar, "20 000", "12 000"), (flavour, "50", "1 500,50"),
                                                      (None, "", "")], lead_days="45"))  # fmt: skip
    order = MaterialOrder.objects.get()
    assert response["Location"] == reverse("production:order", args=[order.pk])
    assert (order.expected_date, order.currency, order.created_by) == (TODAY + datetime.timedelta(days=45), "USD",
                                                                      planner)  # fmt: skip
    assert order.number == f"ЗК-{order.pk:05d}"
    assert [(line.material, line.quantity, line.amount) for line in order.lines.all()] == [
        (sugar, D(20000), D(12000)), (flavour, D(50), D("1500.50")),
    ]  # fmt: skip
    assert order.total == D("13500.50") and material_stock() == {}  # an order doesn't move stock


def test_the_arrival_is_needed_and_not_before_the_order(client, planner, supplier, sugar):
    client.force_login(planner)
    assert client.post(NEW, order_data(supplier, [(sugar, "1", "")])).context["form"].errors["expected_date"]
    yesterday = (TODAY - datetime.timedelta(days=1)).isoformat()
    response = client.post(NEW, order_data(supplier, [(sugar, "1", "")], expected_date=yesterday))
    assert response.context["form"].errors["expected_date"]
    assert not MaterialOrder.objects.exists()


# --- In transit on the Raw materials page ----------------------------------------------------------


def test_materials_page_shows_what_is_on_the_way_and_when(client, planner, supplier, sugar, flavour):
    soon, later = TODAY + datetime.timedelta(days=5), TODAY + datetime.timedelta(days=40)
    make_order(supplier, [(sugar, "1000", "0")], later, MaterialOrder.Status.IN_TRANSIT)
    make_order(supplier, [(sugar, "500", "0"), (flavour, "10", "0")], soon, MaterialOrder.Status.CUSTOMS)
    make_order(supplier, [(flavour, "99", "0")], soon, MaterialOrder.Status.CANCELLED)  # not on the way
    transit = materials_in_transit()
    assert (transit[sugar.pk]["quantity"], transit[sugar.pk]["expected"]) == (D(1500), soon)
    assert transit[flavour.pk]["quantity"] == D(10) and not transit[flavour.pk]["late"]
    client.force_login(planner)
    rows = {row["material"]: row for row in client.get(reverse("production:materials")).context["rows"]}
    assert rows[sugar]["transit"]["quantity"] == D(1500)


def test_a_late_order_is_marked(client, planner, supplier, sugar):
    order = make_order(supplier, [(sugar, "1000", "0")], TODAY - datetime.timedelta(days=3))
    assert order.is_late and order.days_late == 3
    assert materials_in_transit()[sugar.pk]["late"]
    client.force_login(planner)
    page = client.get(reverse("production:orders"))
    assert page.context["late_count"] == 1
    assert [o.pk for o in client.get(reverse("production:orders"), {"late": "1"}).context["page_obj"]] == [order.pk]


# --- Status and receiving ----------------------------------------------------------------------------


def test_status_follows_the_goods_and_a_cancelled_order_leaves_the_list(client, planner, supplier, sugar):
    order = make_order(supplier, [(sugar, "1000", "0")], TODAY + datetime.timedelta(days=9))
    client.force_login(planner)
    client.post(reverse("production:order_status", args=[order.pk]), {"status": "customs"})
    order.refresh_from_db()
    assert order.status == MaterialOrder.Status.CUSTOMS
    client.post(reverse("production:order_status", args=[order.pk]), {"status": "cancelled"})
    assert not client.get(reverse("production:orders")).context["page_obj"].object_list
    assert materials_in_transit() == {}


def test_receiving_makes_the_receipt_in_uzs_and_puts_the_goods_in_stock(client, planner, supplier, sugar, flavour):
    ExchangeRate.objects.create(date=TODAY - datetime.timedelta(days=2), rate=D("12100"))
    order = make_order(supplier, [(sugar, "1000", "650"), (flavour, "10", "120")], TODAY)
    client.force_login(planner)
    detail = client.get(reverse("production:order", args=[order.pk]))
    assert detail.context["receive"].initial["rate"] == D("12100")  # the day's USD rate
    sugar_line, flavour_line = order.lines.all()
    # Nothing assumed: quantities and the supplier's invoice number are typed (the page's "all" fills a line).
    assert f"line_{sugar_line.pk}" not in detail.context["receive"].initial
    assert "number" not in detail.context["receive"].initial
    assert 'data-fill="id_line_' in detail.content.decode()
    response = receive(client, order, {sugar_line: "1 000", flavour_line: "10"}, number="inv-77")
    receipt = MaterialReceipt.objects.get()
    assert response["Location"] == reverse("production:order", args=[order.pk])
    assert (receipt.supplier, receipt.number, receipt.order) == (supplier, "INV-77", order)
    assert sorted((line.material.name, line.quantity, line.amount) for line in receipt.lines.all()) == [
        ("Ароматизатор Мята", D(10), D(1458000)), ("Сахар", D(1000), D(7897500)),
    ]  # fmt: skip
    assert material_stock() == {sugar.pk: D(1000), flavour.pk: D(10)}
    order.refresh_from_db()
    assert order.status == MaterialOrder.Status.RECEIVED
    assert materials_in_transit() == {}
    assert client.get(reverse("production:order_edit", args=[order.pk]))["Location"].endswith(f"/{order.pk}/")


def test_receiving_asks_for_the_rate_and_a_new_invoice_number(client, planner, supplier, sugar):
    MaterialReceipt.objects.create(supplier=supplier, date=TODAY, number="INV-1")
    order = make_order(supplier, [(sugar, "1000", "650")], TODAY)
    client.force_login(planner)
    [line] = order.lines.all()
    response = receive(client, order, {line: "1000"}, number="inv-1", rate="")
    assert set(response.context["receive"].errors) == {"number", "rate"}
    nothing = receive(client, order, {line: "0"}, number="INV-2")
    assert nothing.context["receive"].non_field_errors()  # at least one material must have arrived
    assert MaterialReceipt.objects.count() == 1
    sum_order = make_order(supplier, [(sugar, "5", "100000")], TODAY, currency="UZS")
    assert "rate" not in client.get(reverse("production:order", args=[sum_order.pk])).context["receive"].fields


# --- Goods that travel separately --------------------------------------------------------------------


def test_lines_travel_separately_and_arrive_in_parts(client, planner, supplier, sugar, flavour):
    order = make_order(supplier, [(sugar, "1000", "650"), (flavour, "10", "120")], TODAY + datetime.timedelta(days=20))
    sugar_line, flavour_line = order.lines.all()
    client.force_login(planner)
    later = (TODAY + datetime.timedelta(days=45)).isoformat()
    client.post(reverse("production:order_lines", args=[order.pk]), {
        f"line-{sugar_line.pk}-status": "customs", f"line-{sugar_line.pk}-expected": "",
        f"line-{flavour_line.pk}-status": "in_transit", f"line-{flavour_line.pk}-expected": later,
    })  # fmt: skip
    sugar_line.refresh_from_db()
    flavour_line.refresh_from_db()
    assert (sugar_line.status, flavour_line.status) == ("customs", "in_transit")
    assert flavour_line.expected == TODAY + datetime.timedelta(days=45)
    assert materials_in_transit()[sugar.pk]["customs"] == D(1000)

    # The sugar clears customs in two parts; the flavour is still on its way.
    receive(client, order, {sugar_line: "600", flavour_line: ""}, number="INV-1")
    order.refresh_from_db()
    assert order.status == MaterialOrder.Status.PARTIAL and material_stock() == {sugar.pk: D(600)}
    assert materials_in_transit()[sugar.pk]["quantity"] == D(400)
    [first] = MaterialReceipt.objects.all()
    assert first.lines.get().amount == D("4738500.00")  # 600 of 1 000 for 650 USD, at 12 150
    receive(client, order, {sugar_line: "400"}, number="INV-2")
    assert sugar.pk not in materials_in_transit() and materials_in_transit()[flavour.pk]["quantity"] == D(10)
    receive(client, order, {flavour_line: "10"}, number="INV-3")
    order.refresh_from_db()
    assert order.status == MaterialOrder.Status.RECEIVED and order.receipts.count() == 3


def test_cancelling_an_arrival_puts_it_back_on_the_way(client, planner, supplier, sugar):
    order = make_order(supplier, [(sugar, "1000", "650")], TODAY)
    [line] = order.lines.all()
    client.force_login(planner)
    receive(client, order, {line: "1000"})
    receipt = MaterialReceipt.objects.get()
    client.post(reverse("production:receipt_cancel", args=[receipt.pk]))
    order.refresh_from_db()
    assert order.status == MaterialOrder.Status.IN_TRANSIT and materials_in_transit()[sugar.pk]["quantity"] == D(1000)


def test_what_arrived_can_not_be_edited_away(client, planner, supplier, sugar, flavour):
    order = make_order(supplier, [(sugar, "1000", "650"), (flavour, "10", "120")], TODAY + datetime.timedelta(days=9))
    sugar_line, flavour_line = order.lines.all()
    client.force_login(planner)
    receive(client, order, {sugar_line: "600"})
    data = {
        "supplier": supplier.pk, "number": order.number, "order_date": order.order_date.isoformat(),
        "expected_date": order.expected_date.isoformat(), "lead_days": "", "currency": "USD", "note": "",
        "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "2", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-id": sugar_line.pk, "lines-0-order": order.pk, "lines-0-material": sugar.pk, "lines-0-quantity": "500",
        "lines-0-amount": "650", "lines-0-status": "customs", "lines-0-expected_date": "",
        "lines-1-id": flavour_line.pk, "lines-1-order": order.pk, "lines-1-material": flavour.pk,
        "lines-1-quantity": "10", "lines-1-amount": "120", "lines-1-status": "ordered", "lines-1-expected_date": "",
    }  # fmt: skip
    response = client.post(reverse("production:order_edit", args=[order.pk]), data)
    assert response.context["lines"].forms[0].errors["quantity"]  # 600 arrived already
    response = client.post(reverse("production:order_edit", args=[order.pk]), data | {
        "lines-0-quantity": "1000", "lines-0-DELETE": "on",
    })  # fmt: skip
    assert response.context["lines"].non_form_errors()  # an arrived line stays
    assert "status" not in response.context["form"].fields  # partly received: set by the receipts


# --- The list's Excel and who may do what -------------------------------------------------------------


def test_orders_excel_has_a_row_per_material(client, finance, supplier, sugar, flavour):
    make_order(supplier, [(sugar, "1000", "650"), (flavour, "10", "120")], TODAY + datetime.timedelta(days=3))
    client.force_login(finance)
    response = client.get(reverse("production:orders"), {"export": "xlsx"})
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    assert [(row[5], row[7], row[8]) for row in sheet.iter_rows(min_row=2, values_only=True)] == [
        ("Сахар", 1000, "USD"), ("Ароматизатор Мята", 10, "USD"),
    ]  # fmt: skip


def test_who_works_with_goods_in_transit(client, planner, technologist, finance, operator, supplier, sugar):
    for person, page, new in ((planner, 200, 200), (technologist, 200, 200), (finance, 200, 403), (operator, 403, 403)):
        client.force_login(person)
        assert client.get(reverse("production:orders")).status_code == page, person
        assert client.get(NEW).status_code == new, person


def test_what_arrives_never_adds_up_to_more_than_was_ordered(client, planner, supplier, sugar):
    order = make_order(supplier, [(sugar, "1000", "650")], TODAY)
    [line] = order.lines.all()
    client.force_login(planner)
    too_much = receive(client, order, {line: "1 200"}, number="INV-1")
    assert "1000" in str(too_much.context["receive"].errors[f"line_{line.pk}"])  # at most what was ordered
    receive(client, order, {line: "700"}, number="INV-1")
    again = receive(client, order, {line: "400"}, number="INV-2")  # 300 left
    assert again.context["receive"].errors[f"line_{line.pk}"]
    assert MaterialReceipt.objects.count() == 1 and material_stock()[sugar.pk] == D(700)
    receive(client, order, {line: "300"}, number="INV-2")
    order.refresh_from_db()
    assert order.status == MaterialOrder.Status.RECEIVED and material_stock()[sugar.pk] == D(1000)


# --- Each delivery's "good until" ----------------------------------------------------------------


def test_an_arrival_takes_its_good_until_date_and_the_shelf_life_page_uses_it(client, planner, supplier, sugar):
    client.force_login(planner)
    from finance_saber.production import expiry

    sugar.best_before = TODAY + datetime.timedelta(days=300)  # the material's date: this delivery's wins
    sugar.save()
    order = make_order(supplier, [(sugar, 100, 500)], TODAY)
    line = order.lines.get()
    good_until = TODAY + datetime.timedelta(days=20)
    data = {"date": TODAY.isoformat(), "number": "INV-9", "rate": "12 150", f"line_{line.pk}": "100",
            f"best_before_{line.pk}": good_until.isoformat()}  # fmt: skip
    assert client.post(reverse("production:order_receive", args=[order.pk]), data).status_code == 302
    assert MaterialReceiptLine.objects.get().best_before == good_until
    [lot] = [lot for lot in expiry.material_lots(TODAY) if lot.receipt is not None]
    assert (lot.best_before, lot.status) == (good_until, expiry.Status.SOON)


def test_the_good_until_date_is_not_required_but_not_before_the_arrival(client, planner, supplier, sugar):
    client.force_login(planner)
    order = make_order(supplier, [(sugar, 100, 500)], TODAY)
    line = order.lines.get()
    data = {"date": TODAY.isoformat(), "number": "INV-9", "rate": "12 150", f"line_{line.pk}": "100",
            f"best_before_{line.pk}": (TODAY - datetime.timedelta(days=1)).isoformat()}  # fmt: skip
    response = client.post(reverse("production:order_receive", args=[order.pk]), data)
    assert f"best_before_{line.pk}" in response.context["receive"].errors
    receive(client, order, {line: "100"})  # without a date: fine
    assert MaterialReceiptLine.objects.get().best_before is None
