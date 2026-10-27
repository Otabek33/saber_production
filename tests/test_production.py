"""Production: daily output, stock by recipes, stock pages and movements."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from finance_saber.production import services
from finance_saber.production.models import (
    Material,
    MaterialMove,
    Product,
    ProductionRun,
    ProductMove,
    RecipeLine,
    material_stock,
    product_stock,
)

D = Decimal
DAY = datetime.date(2026, 10, 22)


@pytest.fixture
def sugar(db):
    material = Material.objects.create(name="Сахар", unit="кг")
    MaterialMove.objects.create(material=material, date=DAY, kind=MaterialMove.Kind.OPENING, quantity=D("100"))
    return material


@pytest.fixture
def cans(db):
    material = Material.objects.create(name="Банка", unit="шт", kind=Material.Kind.PACKAGING)
    MaterialMove.objects.create(material=material, date=DAY, kind=MaterialMove.Kind.OPENING, quantity=D("1000"))
    return material


@pytest.fixture
def drink(sugar, cans):
    product = Product.objects.create(name="Green Tea 0,450 л", volume=D("0.45"))
    RecipeLine.objects.create(product=product, material=sugar, quantity_per_unit=D("0.0355"))
    RecipeLine.objects.create(product=product, material=cans, quantity_per_unit=D("1"))
    return product


# --- Recording a day ------------------------------------------------------------------


def test_saving_a_day_adds_goods_and_writes_off_materials(drink, sugar, cans, user):
    services.save_day(DAY, {drink: 600}, user=user)
    assert product_stock() == {drink.pk: D("600")}
    assert material_stock() == {sugar.pk: D("100") - D("21.3"), cans.pk: D("400")}


def test_re_saving_a_day_replaces_its_movements(drink, sugar, cans):
    services.save_day(DAY, {drink: 600})
    services.save_day(DAY, {drink: 100})
    assert ProductionRun.objects.get().quantity == 100
    assert product_stock()[drink.pk] == D("100")
    assert material_stock()[cans.pk] == D("900")


def test_zero_cancels_the_run_and_its_movements(drink, cans):
    services.save_day(DAY, {drink: 600})
    services.save_day(DAY, {drink: 0})
    run = ProductionRun.objects.get()
    assert run.is_cancelled
    assert product_stock().get(drink.pk, 0) == 0
    assert material_stock()[cans.pk] == D("1000")


def test_unchanged_day_changes_nothing(drink):
    services.save_day(DAY, {drink: 600})
    assert services.save_day(DAY, {drink: 600}) == 0


def test_later_recipe_change_does_not_rewrite_saved_days(drink, sugar):
    services.save_day(DAY, {drink: 100})
    RecipeLine.objects.filter(material=sugar).update(quantity_per_unit=D("1"))
    assert material_stock()[sugar.pk] == D("96.45")


def test_history_runs_do_not_move_stock(drink, cans):
    run = ProductionRun.objects.create(date=DAY, product=drink, quantity=500, affects_stock=False)
    run.rebuild_moves()
    assert product_stock() == {}
    assert material_stock()[cans.pk] == D("1000")


def test_material_needs_warn_about_shortage(drink, cans):
    [needs_cans] = [need for need in services.material_needs({drink: 1500}) if need.material == cans]
    assert needs_cans.needed == D("1500")
    assert needs_cans.stock_after == D("-500")
    assert needs_cans.is_short


def test_material_needs_for_a_saved_day_add_its_own_use_back(drink, cans):
    services.save_day(DAY, {drink: 600})
    [needs_cans] = [need for need in services.material_needs({drink: 600}, date=DAY) if need.material == cans]
    assert needs_cans.stock_after == D("400")


def test_units_possible_is_limited_by_the_scarcest_material(drink):
    # Sugar allows 100 / 0.0355 = 2816, cans 1000.
    assert services.units_possible([drink]) == {drink.pk: 1000}


def test_stock_count_books_the_difference(sugar, user):
    move = services.count_stock(sugar, D("90"), DAY, user=user)
    assert (move.kind, move.quantity, move.counted) == (MaterialMove.Kind.ADJUSTMENT, D("-10"), D("90"))
    assert material_stock()[sugar.pk] == D("90")


# --- Pages ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name", ["daily", "journal", "materials", "products", "recipes", "moves", "material_receipt", "shipment"]
)
def test_pages_render(client, user, drink, url_name):
    client.force_login(user)
    assert client.get(reverse(f"production:{url_name}")).status_code == 200


def test_daily_page_saves_when_nothing_runs_short(client, user, drink):
    client.force_login(user)
    url = f"{reverse('production:daily')}?date={DAY.isoformat()}"
    response = client.post(url, {f"product_{drink.pk}": "600"})
    assert response.status_code == 302
    assert ProductionRun.objects.get().quantity == 600


def test_daily_page_asks_before_going_below_zero(client, user, drink):
    client.force_login(user)
    url = f"{reverse('production:daily')}?date={DAY.isoformat()}"
    response = client.post(url, {f"product_{drink.pk}": "1500"})
    assert response.status_code == 200
    assert response.context["shortage"]
    assert not ProductionRun.objects.exists()

    response = client.post(url, {f"product_{drink.pk}": "1500", "confirm": "1"})
    assert response.status_code == 302
    assert ProductionRun.objects.get().quantity == 1500


def test_journal_shows_the_month_grid(client, user, drink):
    services.save_day(DAY, {drink: 600})
    client.force_login(user)
    response = client.get(reverse("production:journal"), {"month": "2026-10"})
    [row] = response.context["rows"]
    assert row["cells"][21] == 600
    assert row["total"] == 600
    assert response.context["grand_total"] == 600


def test_receipt_form_adds_stock(client, user, sugar):
    client.force_login(user)
    response = client.post(
        reverse("production:material_receipt"),
        {"date": DAY.isoformat(), "material": sugar.pk, "quantity": "1 250,5", "note": "Поставщик"},
    )
    assert response.status_code == 302
    assert material_stock()[sugar.pk] == D("1350.5")


def test_shipment_takes_goods_out(client, user, drink):
    services.save_day(DAY, {drink: 600})
    client.force_login(user)
    client.post(reverse("production:shipment"), {"date": DAY.isoformat(), "product": drink.pk, "quantity": "240"})
    assert product_stock()[drink.pk] == D("360")
    assert ProductMove.objects.filter(kind=ProductMove.Kind.SHIPMENT).get().quantity == D("-240")


def test_stock_count_page(client, user, sugar):
    client.force_login(user)
    response = client.post(
        reverse("production:stock_count", args=["material"]),
        {"date": DAY.isoformat(), "item": sugar.pk, "counted": "95"},
    )
    assert response.status_code == 302
    assert material_stock()[sugar.pk] == D("95")


def test_manual_movement_can_be_cancelled_and_restored(client, user, sugar):
    client.force_login(user)
    move = MaterialMove.objects.get()
    url = reverse("production:move_cancel", args=["materials", move.pk])
    client.post(url)
    assert material_stock().get(sugar.pk, 0) == 0
    client.post(url)
    assert material_stock()[sugar.pk] == D("100")


def test_production_movements_cannot_be_cancelled_by_hand(client, user, drink):
    services.save_day(DAY, {drink: 10})
    client.force_login(user)
    move = ProductMove.objects.get()
    assert client.post(reverse("production:move_cancel", args=["products", move.pk])).status_code == 404


def test_recipe_page_saves_norms(client, user, drink, sugar, cans):
    client.force_login(user)
    lines = list(drink.recipe.order_by("id"))
    data = {
        "name": drink.name,
        "sales_name": "",
        "volume": "0.45",
        "position": "0",
        "is_active": "on",
        "recipe-TOTAL_FORMS": "2",
        "recipe-INITIAL_FORMS": "2",
        "recipe-MIN_NUM_FORMS": "1",
        "recipe-MAX_NUM_FORMS": "1000",
    }
    for index, line in enumerate(lines):
        data |= {
            f"recipe-{index}-id": line.pk,
            f"recipe-{index}-product": drink.pk,
            f"recipe-{index}-material": line.material_id,
            f"recipe-{index}-quantity_per_unit": "0,04" if line.material == sugar else "1",
            f"recipe-{index}-position": "0",
        }
    response = client.post(reverse("production:recipe_edit", args=[drink.pk]), data)
    assert response.status_code == 302
    assert drink.recipe.get(material=sugar).quantity_per_unit == D("0.04")
