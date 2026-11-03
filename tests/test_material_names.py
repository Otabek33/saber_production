"""Official material names (production/material_names.py) applied in place by migration 0009."""

import datetime
import importlib
from decimal import Decimal

from django.apps import apps

from finance_saber.production import services
from finance_saber.production.material_names import OFFICIAL, official_name
from finance_saber.production.models import Material, MaterialMove, Product, RecipeLine, material_stock

D = Decimal
DAY = datetime.date(2026, 10, 1)
migration = importlib.import_module("finance_saber.production.migrations.0009_official_material_names")


def apply():
    migration.official_names(apps, None)


def material(name, unit="кг", kind=Material.Kind.RAW, stock=None):
    created = Material.objects.create(name=name, unit=unit, kind=kind)
    if stock:
        MaterialMove.objects.create(material=created, date=DAY, kind=MaterialMove.Kind.OPENING, quantity=D(stock))
    return created


def test_a_renamed_material_keeps_its_stock_and_recipe_norm(db):
    sugar = material("Сахар", stock="100")
    tea = Product.objects.create(name="Green Tea мёд 0,450 л", volume=D("0.45"))
    RecipeLine.objects.create(product=tea, material=sugar, quantity_per_unit=D("0.03555"))
    apply()
    sugar.refresh_from_db()
    assert sugar.name == "Сахар-песок белый кристаллический"
    assert material_stock()[sugar.pk] == D("100")
    assert tea.recipe.get().material == sugar


def test_flavoured_energy_cans_merge_into_one_can(db):
    can, mango_can = (material(name, "шт", Material.Kind.PACKAGING, "100") for name in (
        "7 Energy Drink банка 0,450 л", "7 Energy Drink mango банка 0,450 л",
    ))  # fmt: skip
    plain, mango = (Product.objects.create(name=name, volume=D("0.45")) for name in ("Energy", "Energy mango"))
    RecipeLine.objects.create(product=plain, material=can, quantity_per_unit=1)
    RecipeLine.objects.create(product=mango, material=mango_can, quantity_per_unit=1)
    services.save_day(DAY, {plain: 10, mango: 5})  # both cans in production history
    apply()
    [merged] = Material.objects.filter(name="Банки алюминевые Energy Drink 0,45л")
    assert merged.pk == can.pk and not Material.objects.filter(pk=mango_can.pk).exists()
    assert material_stock()[merged.pk] == D("185")  # 200 in, 15 used
    assert {line.product for line in merged.recipe_lines.all()} == {plain, mango}
    assert merged.run_lines.count() == 2


def test_materials_not_on_the_list_leave_the_recipes_and_history_keeps_them(db):
    glue = material("клей")
    drink = Product.objects.create(name="B Fresh", volume=D("0.45"))
    RecipeLine.objects.create(product=drink, material=glue, quantity_per_unit=D("0.001"))
    services.save_day(DAY, {drink: 10})  # a day made with glue
    apply()
    assert not drink.recipe.exists()
    glue.refresh_from_db()
    assert not glue.is_active  # production history refers to it: kept, out of use
    assert glue.run_lines.count() == 1


def test_a_retired_material_without_history_is_deleted(db):
    material("стрич")
    apply()
    assert not Material.objects.filter(name="стрич").exists()


def test_every_official_material_exists_afterwards(db):
    material("Сахар")
    apply()
    assert set(Material.objects.values_list("name", flat=True)) == {name for name, _unit, _kind in OFFICIAL}
    label = Material.objects.get(name="Самоклеющаяся этикетка 15*15")
    assert (label.unit, label.kind) == ("шт", Material.Kind.PACKAGING)


def test_an_empty_database_is_left_to_the_project_files(db):
    apply()
    assert not Material.objects.exists()


def test_official_name():
    assert official_name("Золотой кришка 202") == "Алюминиевая крышка тип 202 gold"
    assert official_name("Алюминиевая крышка тип 202 gold") == "Алюминиевая крышка тип 202 gold"
    assert official_name("плёнка") is None
