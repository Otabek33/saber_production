"""Shipments: a customer from the company handbook, a waybill with product lines, and which batches went where."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.urls import reverse

from finance_saber.cashbook.models import Company
from finance_saber.production import services, traceability
from finance_saber.production.models import Product, ProductMove, ProductShipment, product_stock

D = Decimal
DAY = {n: datetime.date(2026, 10, n) for n in range(1, 31)}
NEW = reverse("production:shipment")


@pytest.fixture
def goods(db):
    """Tea: batches of 600 (the 2nd) and 400 (the 4th); juice: 100 (the 2nd)."""
    tea = Product.objects.create(name="Чай", volume=D("0.45"))
    juice = Product.objects.create(name="Сок", volume=D("1"))
    services.save_day(DAY[2], {tea: 600, juice: 100})
    services.save_day(DAY[4], {tea: 400})
    return tea, juice


@pytest.fixture
def shop(db):
    return Company.objects.create(name="ООО Магазин", inn="770000001")


@pytest.fixture
def market(db):
    return Company.objects.create(name="ООО Рынок", inn="770000002")


def data(customer, lines, number="", date=DAY[10], **extra):
    form = {
        "customer": customer.pk if customer else "", "number": number, "date": date.isoformat(), "note": "",
        "lines-TOTAL_FORMS": str(len(lines)), "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000", **extra,
    }  # fmt: skip
    for index, (product, quantity) in enumerate(lines):
        form[f"lines-{index}-product"] = product.pk if product else ""
        form[f"lines-{index}-quantity"] = quantity
    return form


def ship(client, *args, **kwargs):
    return client.post(NEW, data(*args, **kwargs))


# --- Entering a shipment -------------------------------------------------------------------------


def test_one_waybill_takes_every_product_out_of_stock(client, technologist, goods, shop):
    tea, juice = goods
    client.force_login(technologist)
    response = ship(client, shop, [(tea, "1 000"), (juice, "100")], number="нк-5")
    shipment = ProductShipment.objects.get()
    assert response["Location"] == reverse("production:shipment_detail", args=[shipment.pk])
    assert (shipment.customer, shipment.number, shipment.created_by) == (shop, "НК-5", technologist)
    assert [(line.product, line.quantity) for line in shipment.lines.all()] == [(tea, 1000), (juice, 100)]
    assert (product_stock()[tea.pk], product_stock()[juice.pk]) == (0, 0)
    moves = ProductMove.objects.filter(kind=ProductMove.Kind.SHIPMENT)
    assert {move.note for move in moves} == {"ООО Магазин, № НК-5"}
    assert all(move.made_by_document for move in moves)  # they change only with the document


def test_a_waybill_without_a_number_is_numbered(client, technologist, goods, shop):
    tea, _juice = goods
    client.force_login(technologist)
    ship(client, shop, [(tea, "10")])
    shipment = ProductShipment.objects.get()
    assert shipment.number == f"ОТ-{shipment.pk:05d}"


def test_customer_is_required_and_comes_from_the_handbook_without_our_company(client, technologist, goods, shop):
    tea, _juice = goods
    own = Company.objects.create(name="ООО Наша", inn="305900839", is_own=True)
    client.force_login(technologist)
    page = client.get(NEW).content.decode()
    assert f'<option value="{shop.pk}">ООО Магазин</option>' in page
    assert "ООО Наша" not in page
    assert "data-choose-first" in page and "data-after-choice hidden" in page
    assert set(ship(client, None, [(tea, "10")]).context["form"].errors) == {"customer"}
    assert ship(client, own, [(tea, "10")]).status_code == 200
    assert not ProductShipment.objects.exists()


def test_the_same_waybill_number_is_used_once(client, technologist, goods, shop, market):
    tea, _juice = goods
    client.force_login(technologist)
    ship(client, shop, [(tea, "10")], number="НК-1")
    response = ship(client, market, [(tea, "10")], number="нк-1")
    assert response.context["form"].errors["number"]
    assert ProductShipment.objects.count() == 1


def test_the_same_product_twice_is_refused(client, technologist, goods, shop):
    tea, _juice = goods
    client.force_login(technologist)
    response = ship(client, shop, [(tea, "10"), (tea, "5")])
    assert response.context["lines"].forms[1].errors["product"]
    assert not ProductShipment.objects.exists()


def test_shipping_more_than_in_stock_asks_first(client, technologist, goods, shop):
    tea, _juice = goods
    client.force_login(technologist)
    response = ship(client, shop, [(tea, "1 500")])  # 1000 in stock
    assert response.status_code == 200
    assert response.context["short"] == [(tea, D(1500), D(1000))]
    assert "Всё равно отгрузить" in response.content.decode()
    assert not ProductShipment.objects.exists()
    assert ship(client, shop, [(tea, "1 500")], confirm="1").status_code == 302
    assert product_stock()[tea.pk] == -500


def test_a_removed_line_is_not_shipped(client, technologist, goods, shop):
    tea, juice = goods
    client.force_login(technologist)
    ship(client, shop, [(tea, "10"), (juice, "abc")], **{"lines-1-DELETE": "on"})
    assert [line.product for line in ProductShipment.objects.get().lines.all()] == [tea]


def test_cancelling_returns_the_goods_and_restoring_takes_them_again(client, technologist, goods, shop):
    tea, _juice = goods
    client.force_login(technologist)
    ship(client, shop, [(tea, "300")])
    shipment = ProductShipment.objects.get()
    client.post(reverse("production:shipment_cancel", args=[shipment.pk]))
    assert product_stock()[tea.pk] == 1000
    client.post(reverse("production:shipment_cancel", args=[shipment.pk]))
    assert product_stock()[tea.pk] == 700


# --- What each customer got ------------------------------------------------------------------------


@pytest.fixture
def shipped(client, technologist, goods, shop, market):
    tea, juice = goods
    client.force_login(technologist)
    ship(client, shop, [(tea, "700"), (juice, "40")], number="A-1", date=DAY[5])
    ship(client, market, [(tea, "200")], number="B-1", date=DAY[6])
    ship(client, shop, [(tea, "50")], number="A-2", date=DAY[20])
    return {doc.number: doc for doc in ProductShipment.objects.all()}


def journal(client, **params):
    response = client.get(reverse("production:shipments"), params)
    assert response.status_code == 200
    return response


def test_journal_by_customer_with_totals_per_product(client, finance, shop, shipped, goods):
    tea, juice = goods
    client.force_login(finance)  # finance sees what each customer got
    response = journal(client, customer=shop.pk)
    assert [doc.number for doc in response.context["page_obj"]] == ["A-2", "A-1"]
    assert {t["product__name"]: t["units"] for t in response.context["totals"]} == {"Чай": 750, "Сок": 40}
    assert [d.number for d in journal(client, product=juice.pk).context["page_obj"]] == ["A-1"]
    period = journal(client, **{"from": DAY[6].isoformat(), "to": DAY[10].isoformat()})
    assert [d.number for d in period.context["page_obj"]] == ["B-1"]


def test_excel_has_one_row_per_product(client, finance, shop, shipped):
    client.force_login(finance)
    response = client.get(reverse("production:shipments_export"), {"customer": shop.pk})
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    rows = [(row[1], row[2], row[6], row[7]) for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert rows == [
        ("A-2", "ООО Магазин", "Чай", 50),
        ("A-1", "ООО Магазин", "Чай", 700),
        ("A-1", "ООО Магазин", "Сок", 40),
    ]


def test_company_page_links_the_customers_shipments(client, finance, shop, shipped):
    client.force_login(finance)
    page = client.get(shop.get_absolute_url()).content.decode()
    assert f"{reverse('production:shipments')}?customer={shop.pk}" in page


# --- Which batches went to which customer (recall) ---------------------------------------------------


def test_a_shipment_shows_the_batches_it_carried_oldest_first(client, technologist, shipped, goods):
    tea, _juice = goods
    first, second = (services.ProductionRun.objects.get(product=tea, date=day) for day in (DAY[2], DAY[4]))
    batches = traceability.shipment_batches(shipped["A-1"])
    tea_line = shipped["A-1"].lines.get(product=tea)
    assert [(piece.lot.run, piece.quantity) for piece in batches[tea_line.pk]] == [(first, 600), (second, 100)]
    client.force_login(technologist)
    page = client.get(reverse("production:shipment_detail", args=[shipped["A-1"].pk])).content.decode()
    assert traceability.batch_code(first) in page and traceability.batch_code(second) in page


def test_a_batch_shows_who_got_it(client, technologist, shipped, goods, shop, market):
    tea, _juice = goods
    second = services.ProductionRun.objects.get(product=tea, date=DAY[4])  # 400: 100 to A-1, 200 to B-1, 50 to A-2
    shipping = traceability.batch_destinations(second)
    assert [(d.customer, d.document, d.quantity) for d in shipping.destinations] == [
        ("ООО Магазин", "Отгрузка № A-1", 100), ("ООО Рынок", "Отгрузка № B-1", 200),
        ("ООО Магазин", "Отгрузка № A-2", 50),
    ]  # fmt: skip
    assert shipping.remaining == 50
    client.force_login(technologist)
    page = client.get(reverse("production:batch", args=[second.pk])).content.decode()
    assert "Кому отгружена партия" in page and "ООО Рынок" in page


# --- Who may do what ---------------------------------------------------------------------------------


def test_sales_people_enter_shipments(client, finance, operator, shipped):
    """Shipments are the sales: finance and the operator enter and load them, as they did the daily sales."""
    for person in (finance, operator):
        client.force_login(person)
        assert client.get(NEW).status_code == 200
        assert client.get(reverse("production:shipments")).status_code == 200


def test_no_role_has_no_access(client, no_role, shipped):
    client.force_login(no_role)
    assert client.get(reverse("production:shipments")).status_code == 403
