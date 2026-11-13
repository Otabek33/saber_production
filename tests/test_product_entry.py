"""Products: shelf life typed as months, and deleting a product entered by mistake."""

import datetime
import importlib
from decimal import Decimal

import pytest
from django.contrib import messages
from django.urls import reverse

from finance_saber.production.forms import ProductForm
from finance_saber.production.models import Material, Product, ProductMove, RecipeLine
from finance_saber.production.recipe_excel import product_shelf_life

D = Decimal
ADD = reverse("production:product_add")


def _data(**overrides):
    data = {
        "name": "Чай",
        "sales_name": "",
        "volume": "0.5",
        "position": "0",
        "shelf_life_months": "12",
        "is_active": "on",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("months", "text"),
    [(1, "1 месяц"), (2, "2 месяца"), (5, "5 месяцев"), (12, "12 месяцев"), (11, "11 месяцев"), (21, "21 месяц")],
)
def test_months_are_stored_as_text_the_expiry_reader_understands(db, months, text):
    from finance_saber.production.expiry import parse

    form = ProductForm(_data(shelf_life_months=str(months)))
    assert form.is_valid(), form.errors
    product = form.save()
    assert product.shelf_life == text
    assert parse(product.shelf_life) == (months, "months")


def test_empty_shelf_life_is_allowed(db):
    form = ProductForm(_data(shelf_life_months=""))
    assert form.is_valid(), form.errors
    assert form.save().shelf_life == ""


def test_existing_years_show_as_months(db):
    product = Product.objects.create(name="Сок", volume=D("1"), shelf_life="2 года")
    assert ProductForm(instance=product).initial["shelf_life_months"] == 24


def test_zero_months_is_rejected(db):
    assert not ProductForm(_data(shelf_life_months="0")).is_valid()


def test_unused_product_is_deleted_with_its_recipe(admin_client, db):
    product = Product.objects.create(name="Ошибка", volume=D("1"))
    sugar = Material.objects.create(name="Сахар", unit="кг")
    RecipeLine.objects.create(product=product, material=sugar, quantity_per_unit=D("0.1"))

    response = admin_client.post(reverse("production:product_delete", args=[product.pk]))

    assert response.status_code == 302
    assert not Product.objects.filter(pk=product.pk).exists()
    assert not RecipeLine.objects.filter(material=sugar).exists()
    assert Material.objects.filter(pk=sugar.pk).exists()


def test_product_with_stock_history_is_kept(admin_client, db):
    product = Product.objects.create(name="Прод", volume=D("1"))
    ProductMove.objects.create(
        product=product, date=datetime.date(2026, 10, 1), kind=ProductMove.Kind.OUTPUT, quantity=D(5)
    )

    response = admin_client.post(reverse("production:product_delete", args=[product.pk]), follow=True)

    assert Product.objects.filter(pk=product.pk).exists()
    assert [message.level for message in response.context["messages"]] == [messages.ERROR]


def test_delete_needs_a_post(admin_client, db):
    product = Product.objects.create(name="Ничего", volume=D("1"))
    assert admin_client.get(reverse("production:product_delete", args=[product.pk])).status_code == 405
    assert Product.objects.filter(pk=product.pk).exists()


# --- Shelf life of the products already loaded (migration 0025), and the products Excel ---------


@pytest.mark.parametrize(
    ("name", "shelf_life"),
    [
        ("B Fresh анор 0,450 л Be Fresh Гранат 450 ML 1/12", "24 месяца"),
        ("7 Energy Drink 0,450 л 7 Energy 450 ML 1/12", "18 месяцев"),
        ("Green Tea мёд 0,450 л Sakura Зеленый чай с мёдом 1/12 0.5 L", "12 месяцев"),
        ("Blak Tea лимон-мёд-имбир 0,450 л", "12 месяцев"),
        ("Вода 1 л", None),
    ],
)
def test_brands_get_their_shelf_life(name, shelf_life):
    migration = importlib.import_module("finance_saber.production.migrations.0025_brand_shelf_life")
    assert migration.brand_shelf_life(name) == shelf_life


@pytest.mark.parametrize(
    ("cell", "text"),
    [(24, "24 месяца"), ("18", "18 месяцев"), (12.0, "12 месяцев"), ("2 года", "2 года"), ("", ""), (None, "")],
)
def test_products_excel_shelf_life_is_months(cell, text):
    assert product_shelf_life(cell) == text


@pytest.mark.parametrize("cell", ["0", "1,5", "скоро"])
def test_products_excel_refuses_an_unreadable_shelf_life(cell):
    with pytest.raises(ValueError):
        product_shelf_life(cell)
