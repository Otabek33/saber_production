"""Shipments loaded from SmartUp's sales report: reading, matching names once, loading and loading again."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from finance_saber.cashbook.models import Company, CompanyAlias
from finance_saber.production import services, shipment_import
from finance_saber.production.models import (
    Product,
    ProductAlias,
    ProductMove,
    ProductShipment,
    SkippedName,
    product_stock,
)

D = Decimal
UPLOAD, REVIEW = reverse("production:shipments_import"), reverse("production:shipments_import_review")
HEADER = [
    "Дата доставки", "Организация", "Название проекта", "ТМЦ", "Клиент", "Количество продажи",
    "Сумма со скидкой/наценкой в базовой валюте",
]  # fmt: skip
WATER = 'Вода питьевая TM "SABER WATER" 0,33'
BARAKA, TEA = '"BARAKA WATERS GROUP" MCHJ (САМАРКАНД)', "ЖБ 450 Мл Sakura Зеленый чай с мёдом"


def report(rows, total=True):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Конструктор отчётов по продажам"
    for line in ("Дата доставки:  26.10.2026-31.10.2026", "Организация:  BON NATURAL DRINK", "", "Статус:  Архив"):
        sheet.append([line])
    sheet.append(HEADER)
    for row in rows:
        sheet.append([row[0], "BON NATURAL DRINK", *row[1:]])
    if total:
        sheet.append(["ИТОГО"] * 5 + [0, 0])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def goods(db):
    """Tea and juice in stock: 1000 and 500 units."""
    tea = Product.objects.create(name="Green Tea", sales_name="ЖБ 450 Мл Sakura Зеленый чай с мёдом", volume=D("0.45"))
    juice = Product.objects.create(name="B Fresh", sales_name="Be Fresh Гранат 450 ML 1/12", volume=D("0.45"))
    services.save_day(datetime.date(2026, 10, 1), {tea: 1000, juice: 500})
    return tea, juice


@pytest.fixture
def shops(db):
    baraka = Company.objects.create(name='"BARAKA WATERS GROUP" MCHJ (САМАРКАНД)', inn="310950854")
    havas = Company.objects.create(name='(РЦ) ООО "HAVAS FOOD"', inn="305645847")
    return baraka, havas


ROWS = [
    # Spaced differently from the handbook, as SmartUp writes them.
    ("26.10.2026", "Первичные отгрузки", "Be Fresh Гранат  450 ML 1/12", BARAKA.replace(" (", "  ("), 100, 724167.0),
    ("26.10.2026", "Первичные отгрузки", TEA.replace("ЖБ ", "ЖБ  "), BARAKA, 200, 1053400.0),
    ("26.10.2026", "Key Account", TEA, BARAKA, 50, 263350.0),
    ("26.10.2026", "Key Account", TEA, BARAKA, None, None),  # listed, nothing shipped
    ("30.10.2026", "Другие", "Be Fresh Гранат 450 ML 1/12", '(РЦ)  ООО "HAVAS  FOOD"', -10, -72416.7),
]  # fmt: skip


def test_report_rows_skip_empty_lines_and_the_total(goods):
    parsed = shipment_import.read_report(BytesIO(report(ROWS)))
    assert parsed.title[0] == "Дата доставки: 26.10.2026-31.10.2026"
    assert [(row.row, row.quantity, row.amount) for row in parsed.rows] == [
        (6, 100, D("724167.00")), (7, 200, D("1053400.00")), (8, 50, D("263350.00")), (10, -10, D("-72416.70")),
    ]  # fmt: skip
    assert parsed.errors == []


def test_unreadable_rows_are_listed(goods):
    bad = [("32.10.2026", "X", "Be Fresh Гранат 450 ML 1/12", "Кто-то", 5, 100), ("26.10.2026", "X", "", "Кто", 5, -1)]
    parsed = shipment_import.read_report(BytesIO(report(bad)))
    assert parsed.rows == [] and len(parsed.errors) == 2


def test_a_file_without_the_columns_is_refused():
    book = openpyxl.Workbook()
    book.active.append(["Товар", "Кол-во"])
    buffer = BytesIO()
    book.save(buffer)
    with pytest.raises(shipment_import.ReportError):
        shipment_import.read_report(BytesIO(buffer.getvalue()))


def test_names_match_whatever_the_spacing(goods, shops):
    tea, juice = goods
    baraka, havas = shops
    matching = shipment_import.match(shipment_import.read_report(BytesIO(report(ROWS))).rows)
    assert matching.complete
    assert set(matching.products.values()) == {tea, juice}
    assert set(matching.customers.values()) == {baraka, havas}


def upload(client, rows):
    return client.post(UPLOAD, {"file": SimpleUploadedFile("report.xlsx", report(rows))})


def test_load_makes_one_document_per_day_customer_project_and_direction(client, technologist, goods, shops):
    tea, juice = goods
    baraka, havas = shops
    client.force_login(technologist)
    assert upload(client, ROWS)["Location"] == REVIEW
    review = client.get(REVIEW)
    assert review.context["counts"] == {"new": 3, "changed": 0, "same": 0, "cancelled": 0}
    response = client.post(REVIEW, {"load": "1"})
    assert response["Location"] == reverse("production:shipments") + "?from=2026-10-26&to=2026-10-30"

    first, second = ProductShipment.objects.filter(customer=baraka).order_by("channel")
    assert (first.channel, second.channel) == ("Key Account", "Первичные отгрузки")
    assert [(line.product, line.quantity, line.amount) for line in second.lines.all()] == [
        (juice, 100, D("724167.00")), (tea, 200, D("1053400.00")),
    ]  # fmt: skip
    assert second.source == ProductShipment.Source.SMARTUP and second.number.startswith("ОТ-")
    returned = ProductShipment.objects.get(customer=havas)
    assert returned.is_return and returned.number.startswith("ВЗ-")
    assert [(line.quantity, line.amount, line.price) for line in returned.lines.all()] == [
        (10, D("72416.70"), D("7241.67"))
    ]
    assert product_stock()[tea.pk] == 1000 - 250
    assert product_stock()[juice.pk] == 500 - 100 + 10  # the return comes back
    assert ProductMove.objects.filter(kind=ProductMove.Kind.RETURN).get().quantity == 10


def test_loading_the_report_again_updates_instead_of_doubling(client, technologist, goods, shops):
    tea, _juice = goods
    client.force_login(technologist)
    upload(client, ROWS)
    client.post(REVIEW, {"load": "1"})
    upload(client, ROWS)
    assert client.get(REVIEW).context["counts"] == {"new": 0, "changed": 0, "same": 3, "cancelled": 0}

    corrected = [ROWS[0], ROWS[1][:4] + (180, 948060.0), *ROWS[2:]]
    upload(client, corrected)
    review = client.get(REVIEW)
    assert review.context["counts"]["changed"] == 1
    client.post(REVIEW, {"load": "1"})
    assert ProductShipment.objects.count() == 3
    assert product_stock()[tea.pk] == 1000 - 230


def test_a_document_cancelled_here_stays_cancelled(client, technologist, goods, shops):
    _tea, juice = goods
    client.force_login(technologist)
    upload(client, ROWS)
    client.post(REVIEW, {"load": "1"})
    returned = ProductShipment.objects.get(is_return=True)
    client.post(reverse("production:shipment_cancel", args=[returned.pk]))
    upload(client, ROWS)
    assert client.get(REVIEW).context["counts"]["cancelled"] == 1
    client.post(REVIEW, {"load": "1"})
    returned.refresh_from_db()
    assert returned.is_cancelled and product_stock()[juice.pk] == 400


def test_new_names_are_matched_once_and_remembered(client, technologist, goods, shops):
    tea, _juice = goods
    baraka, _havas = shops
    rows = [
        ("27.10.2026", "Key Account", "Sakura с мёдом (новое имя)", "BARAKA Самарканд", 12, 63204.0),
        ("27.10.2026", "Key Account", WATER, "Новый магазин", 24, 48000.0),
    ]
    client.force_login(technologist)
    upload(client, rows)
    review = client.get(REVIEW)
    assert review.context["matching"].unknown_products == ["Sakura с мёдом (новое имя)", WATER]
    assert review.context["matching"].unknown_customers == ["BARAKA Самарканд", "Новый магазин"]
    assert client.post(REVIEW, {"match": "1", "product-0": tea.pk}).status_code == 302
    assert not ProductAlias.objects.filter(name="Sakura с мёдом (новое имя)").exists()  # all or nothing
    client.post(
        REVIEW, {"match": "1", "product-0": tea.pk, "product-1": "skip", "customer-0": baraka.pk, "customer-1": "new"}
    )
    assert ProductAlias.objects.get(name="Sakura с мёдом (новое имя)").product == tea
    assert SkippedName.objects.filter(name=WATER).exists()
    assert CompanyAlias.objects.get(name="BARAKA Самарканд").company == baraka
    assert Company.objects.filter(name="Новый магазин").exists()

    review = client.get(REVIEW)
    assert review.context["plan"].skipped_rows == 1  # the water
    client.post(REVIEW, {"load": "1"})
    assert ProductShipment.objects.get().customer == baraka
    upload(client, rows)  # the next report: matched by itself
    assert client.get(REVIEW).context["matching"].complete


def test_shortage_is_shown_before_loading(client, technologist, goods, shops):
    client.force_login(technologist)
    upload(client, [("26.10.2026", "X", "Be Fresh Гранат 450 ML 1/12", BARAKA, 800, 1)])
    [(product, units, have, after)] = client.get(REVIEW).context["short"]
    assert (product.name, units, have, after) == ("B Fresh", 800, 500, -300)


def test_cancel_forgets_the_file(client, technologist, goods, shops):
    client.force_login(technologist)
    upload(client, ROWS)
    client.post(REVIEW, {"discard": "1"})
    assert client.get(REVIEW)["Location"] == UPLOAD
    assert not ProductShipment.objects.exists()


def test_who_loads_shipments(client, finance, operator, no_role):
    for person, status in ((finance, 200), (operator, 200), (no_role, 403)):
        client.force_login(person)
        assert client.get(UPLOAD).status_code == status


def test_a_manual_shipment_has_amounts_and_can_be_a_return(client, technologist, goods, shops):
    tea, _juice = goods
    baraka, _havas = shops
    client.force_login(technologist)
    form = {
        "customer": baraka.pk, "number": "", "date": "2026-10-28", "note": "", "is_return": "on",
        "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-product": tea.pk, "lines-0-quantity": "5 000", "lines-0-amount": "26 335 000",
    }  # fmt: skip
    client.post(reverse("production:shipment"), form)  # more than in stock, but a return: no question
    shipment = ProductShipment.objects.get()
    assert shipment.is_return and shipment.total == D("26335000")
    assert product_stock()[tea.pk] == 6000


def test_an_unfinished_upload_is_pointed_out_until_it_is_loaded(client, technologist, goods, shops):
    tea, juice = goods
    client.force_login(technologist)
    upload(client, ROWS)
    journal = client.get(reverse("production:shipments"))
    assert journal.context["pending_import"]["file"] == "report.xlsx"
    assert reverse("production:shipments_import_review") in journal.content.decode()
    assert product_stock()[tea.pk] == 1000  # read, not loaded: nothing left the warehouse

    client.post(REVIEW, {"load": "1"})
    assert client.get(reverse("production:shipments")).context["pending_import"] is None
    products = {row["product"]: row for row in client.get(reverse("production:products")).context["rows"]}
    assert products[tea]["stock"] == 750
    assert products[juice]["stock"] == 410
