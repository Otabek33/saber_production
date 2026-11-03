"""Sales: the yearly plan next to what was shipped; the old daily sales moved to shipments."""

import datetime
import importlib
from decimal import Decimal

import openpyxl
import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from finance_saber.cashbook.models import Company, Counterparty, Region
from finance_saber.production.importer import key
from finance_saber.production.models import Product, ProductMove, ProductShipment, ProductShipmentLine
from finance_saber.sales import services
from finance_saber.sales.models import Channel, Client, Plan, Sale
from finance_saber.settlements.models import OpeningBalance

D = Decimal
DAY = datetime.date(2026, 10, 5)


@pytest.fixture
def tea(db):
    return Product.objects.create(
        name="Green Tea мёд 0,450 л", sales_name="Sakura Зеленый чай с мёдом", volume=D("0.45")
    )


@pytest.fixture
def energy(db):
    return Product.objects.create(name="7 Energy Drink 0,450 л", sales_name="7 Energy 450 ML 1/12", volume=D("0.45"))


@pytest.fixture
def shop(db):
    return Company.objects.create(name="ООО Магазин", inn="770000001")


def ship(customer, product, quantity, date=DAY, is_return=False, amount="1000"):
    shipment = ProductShipment.objects.create(date=date, customer=customer, is_return=is_return)
    ProductShipmentLine.objects.create(shipment=shipment, product=product, quantity=quantity, amount=D(amount))
    return shipment


def plan_file(tmp_path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Plan 2026"
    sheet["A1"] = 2026
    for title_row, title, values in (
        (4, "Всего план реализации на 2026 год", 100),
        (27, "Всего план производства на 2026 год", 150),
    ):
        sheet.cell(title_row, 2, title)
        sheet.cell(title_row + 1, 3, "Наименование товара")
        for month in range(1, 13):
            sheet.cell(title_row + 1, 3 + month, datetime.datetime(2026, month, 1))
            sheet.cell(title_row + 2, 3 + month, "План")
        for offset, name in enumerate(["Sakura Зеленый чай с мёдом", "7 Energy 450 ML 1/12"]):
            sheet.cell(title_row + 3 + offset, 3, name)
            for month in range(1, 13):
                sheet.cell(title_row + 3 + offset, 3 + month, values * month)
    path = tmp_path / "plan.xlsx"
    book.save(path)
    return path


# --- The plan -----------------------------------------------------------------------------


def test_import_plan(tmp_path, tea, energy):
    report = services.import_plan(services.read_plan(plan_file(tmp_path)))
    assert report.saved and report.year == 2026
    assert Plan.objects.filter(kind=Plan.Kind.SALES).count() == 24
    assert Plan.objects.get(kind=Plan.Kind.PRODUCTION, product=tea, month=3).quantity == 450
    services.import_plan(services.read_plan(plan_file(tmp_path)))  # again: replaces, doesn't add
    assert Plan.objects.count() == 48


def test_plan_fact_is_what_was_shipped_less_returns(client, user, tmp_path, shop, tea, energy):
    services.import_plan(services.read_plan(plan_file(tmp_path)))
    ship(shop, tea, 500)
    ship(shop, tea, 40, is_return=True)
    cancelled = ship(shop, tea, 70)
    cancelled.is_cancelled = True  # not counted
    cancelled.save()
    client.force_login(user)
    response = client.get(reverse("sales:plan"), {"year": 2026})
    row = next(row for row in response.context["rows"] if row["product"] == tea)
    october = row["cells"][9]
    assert (october["plan"], october["fact"], october["percent"]) == (1000, 460, 46)


def test_plan_upload_page(client, finance, tmp_path, tea, energy):
    client.force_login(finance)
    with open(plan_file(tmp_path), "rb") as handle:
        file = SimpleUploadedFile("plan.xlsx", handle.read())
    response = client.post(reverse("sales:import"), {"file": file})
    assert response.context["plan_report"].saved
    assert Plan.objects.count() == 48


def test_plan_can_be_edited(client, finance, tea):
    client.force_login(finance)
    response = client.post(reverse("sales:plan") + "?year=2026&kind=sales", {f"plan-{tea.pk}-11": "1 500"})
    assert response.status_code == 302
    assert Plan.objects.get(product=tea, month=11, year=2026, kind=Plan.Kind.SALES).quantity == 1500


def test_known_aliases_are_seeded_for_the_plan(energy):
    Product.objects.create(name="Blak Tea Апельсин-корица 0,450 л", volume=D("0.45"))
    assert services.seed_product_aliases() == 2  # 7 Energy 1/24 and the orange-cinnamon tea


# --- The old pages lead to their replacements --------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/sales/daily/", "production:shipments"),
        ("/sales/import/", "production:shipments_import"),
        ("/sales/debts/", "settlements:balances"),
        ("/sales/debts/client/5/", "settlements:balances"),
        ("/sales/clients/", "cashbook:companies"),
    ],
)
def test_old_sales_pages_redirect(client, finance, old, new):
    client.force_login(finance)
    assert client.get(old)["Location"] == reverse(new)


def test_no_role_sees_no_plan(client, no_role):
    client.force_login(no_role)
    assert client.get(reverse("sales:plan")).status_code == 403


# --- Moving the old sales to shipments (production migration 0013) -------------------------------

migration = importlib.import_module("finance_saber.production.migrations.0013_sales_to_shipments")


def old_sale(client, product, quantity, amount, date=DAY, channel=None, affects_stock=False):
    sale = Sale.objects.create(
        date=date, client=client, product=product, quantity=quantity, amount=D(amount), channel=channel,
        source=Sale.Source.EXCEL, affects_stock=affects_stock,
    )  # fmt: skip
    sale.rebuild_moves()
    return sale


def test_old_sales_become_one_shipment_per_day_client_and_project(shop, tea, energy):
    key_account = Channel.objects.create(name="Key Account")
    known = Client.objects.create(name="ООО  МАГАЗИН")  # matches the company, whatever the spacing
    old_sale(known, tea, 100, "500000", channel=key_account)
    old_sale(known, energy, 50, "400000", channel=key_account)
    old_sale(known, tea, 10, "50000")  # another project (none)
    migration.copy_sales(apps, None)

    first, second = ProductShipment.objects.filter(customer=shop).order_by("channel")
    assert (first.channel, second.channel) == ("", "Key Account")
    assert sorted((line.product.name, line.quantity, line.amount) for line in second.lines.all()) == [
        ("7 Energy Drink 0,450 л", 50, D("400000")), ("Green Tea мёд 0,450 л", 100, D("500000")),
    ]  # fmt: skip
    assert second.external_key == f"smartup:{DAY.isoformat()}:{key('ООО МАГАЗИН')}:{key('Key Account')}"
    assert not second.affects_stock and not ProductMove.objects.exists()  # history: no stock moves
    migration.copy_sales(apps, None)  # again: nothing doubles
    assert ProductShipment.objects.count() == 2


def test_a_sale_that_counted_in_stock_moves_its_movement_to_the_shipment(shop, tea):
    old_sale(Client.objects.create(name="ООО Магазин"), tea, 100, "500000", affects_stock=True)
    assert ProductMove.objects.get().source.startswith("sale:")
    migration.copy_sales(apps, None)
    shipment = ProductShipment.objects.get()
    move = ProductMove.objects.get()
    assert shipment.affects_stock and move.source.startswith(f"shipment:{shipment.pk}:") and move.quantity == -100


def test_unknown_clients_become_companies_with_their_region_payer_and_opening_balance(tea):
    region, _created = Region.objects.get_or_create(name="Samarqand")
    payer = Counterparty.objects.create(reference="2020/1/PAYER", name="PAYER")
    client = Client.objects.create(name="Новый клиент", region=region, payer=payer, opening_balance=D("250000"))
    old_sale(client, tea, 10, "50000")
    migration.copy_sales(apps, None)
    company = Company.objects.get(name="Новый клиент")
    assert company.region == region
    payer.refresh_from_db()
    assert payer.company == company
    assert OpeningBalance.objects.get(company=company).amount == D("250000")
