"""The production calculation: each production keeps its own recipe, defect and brewed quantity.

The numbers are those of the calculation sheet «Bfresh анор ГП калкульяция 21.10.26г.xlsx».
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from finance_saber.production import services
from finance_saber.production.models import (
    Material,
    MaterialMove,
    Product,
    ProductionRun,
    ProductMove,
    RecipeLine,
    RunLine,
    material_stock,
)

D = Decimal
DAY = datetime.date(2026, 10, 21)


@pytest.fixture
def anor(db):
    product = Product.objects.create(name="B Fresh анор 0,450 л", volume=D("0.45"))
    materials = {
        "sugar": Material.objects.create(name="Сахар", unit="кг"),
        "acid": Material.objects.create(name="Лимоная кислота Е330", unit="кг"),
        "can": Material.objects.create(name="B Fresh анор банка 0,450 л", unit="шт", kind=Material.Kind.PACKAGING),
        "tray": Material.objects.create(name="Гофра поддон 12 шт", unit="шт", kind=Material.Kind.PACKAGING),
    }
    for material in materials.values():
        MaterialMove.objects.create(material=material, date=DAY, kind=MaterialMove.Kind.OPENING, quantity=D(500000))
    # The saved recipe, before this production: 103 kg of sugar per 1000 l.
    RecipeLine.objects.create(product=product, material=materials["sugar"], quantity_per_unit=D("0.04635"), position=1)
    RecipeLine.objects.create(product=product, material=materials["acid"], quantity_per_unit=D("0.00144"), position=2)
    RecipeLine.objects.create(product=product, material=materials["can"], quantity_per_unit=D("1"), position=3)
    RecipeLine.objects.create(
        product=product, material=materials["tray"], quantity_per_unit=D("0.0833333333"), position=4,
        allowed_loss=D("1"),
    )  # fmt: skip
    return product, materials


def url(product, day=DAY):
    return reverse("production:calculation", kwargs={"date": day.isoformat(), "product_pk": product.pk})


def form_data(materials, quantity="166680", batch="166666", sugar="105,6"):
    rows = [
        (materials["sugar"], sugar, "", "2"),
        (materials["acid"], "3,2", "", "2"),
        (materials["can"], "1", "300", "2"),
        (materials["tray"], "1/12", "10", "2"),
    ]
    data = {
        "quantity": quantity,
        "batch_quantity": batch,
        "lines-TOTAL_FORMS": str(len(rows)),
        "lines-INITIAL_FORMS": str(len(rows)),
        "lines-MIN_NUM_FORMS": "1",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, (material, norm, defect, allowed) in enumerate(rows):
        data |= {
            f"lines-{index}-material": material.pk,
            f"lines-{index}-norm": norm,
            f"lines-{index}-defect": defect,
            f"lines-{index}-allowed_loss": allowed,
        }
    return data


def test_page_shows_the_recipe_with_raw_norms_per_1000_litres(client, user, anor):
    product, materials = anor
    client.force_login(user)
    response = client.get(url(product))
    assert response.status_code == 200
    initial = [form.initial for form in response.context["formset"]]
    assert [(row["material"], row["norm"]) for row in initial] == [
        (materials["sugar"], "103"),
        (materials["acid"], "3.2"),
        (materials["can"], "1"),
        (materials["tray"], "0.0833333333"),
    ]
    assert "Калькуляция производства" in response.content.decode()


def test_saving_writes_off_like_the_calculation_sheet(client, user, anor):
    product, materials = anor
    client.force_login(user)
    response = client.post(url(product), form_data(materials))
    assert response.status_code == 302

    run = ProductionRun.objects.get()
    assert (run.quantity, run.batch_quantity, run.brewed) == (166680, 166666, 166666)
    lines = {line.material: line for line in run.lines.all()}
    # Raw materials: norm per 1000 l -> per 0.45 l can, times the cans brewed (Excel G13 = F13 × G12).
    assert lines[materials["sugar"]].quantity_per_unit == D("0.04752")
    assert lines[materials["sugar"]].consumed == D("7919.9683")
    assert lines[materials["acid"]].consumed == D("239.9990")
    # Packaging: per finished can (H21 = F21 × H12), plus the production defect (I21).
    assert lines[materials["can"]].consumed == D(166680 + 300)
    assert lines[materials["tray"]].consumed == D(13890 + 10)
    assert lines[materials["can"]].loss_percent(run) == D("0.18")  # J21: 300 / 166 680 × 100

    stock = material_stock()
    assert stock[materials["sugar"].pk] == D(500000) - D("7919.9683")
    assert stock[materials["can"].pk] == D(500000 - 166980)
    assert ProductMove.objects.get(run=run).quantity == 166680
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert "Рецепт B Fresh анор 0,450 л обновлён: эти нормы будут в следующем выпуске." in messages


def test_the_latest_production_becomes_the_recipe(client, user, anor):
    product, materials = anor
    client.force_login(user)
    client.post(url(product), form_data(materials))
    recipe = {line.material: line for line in product.recipe.all()}
    assert recipe[materials["sugar"]].quantity_per_unit == D("0.04752")  # 105.6 per 1000 l now
    assert recipe[materials["tray"]].allowed_loss == D("2")

    # The next day starts from it.
    response = client.get(url(product, DAY + datetime.timedelta(days=1)))
    assert response.context["formset"].forms[0].initial["norm"] == "105.6"


def test_correcting_an_older_day_keeps_the_current_recipe(client, user, anor):
    product, materials = anor
    client.force_login(user)
    client.post(url(product, DAY + datetime.timedelta(days=1)), form_data(materials, sugar="104"))
    response = client.post(url(product), form_data(materials, sugar="99"))
    assert product.recipe.get(material=materials["sugar"]).quantity_per_unit == D("0.0468")  # 104 per 1000 l
    old_run = ProductionRun.objects.get(date=DAY)
    assert old_run.lines.get(material=materials["sugar"]).quantity_per_unit == D("0.04455")  # 99 per 1000 l
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert "Есть более поздний выпуск этого продукта, поэтому рецепт не изменён." in messages


def test_a_production_keeps_its_recipe_when_the_recipe_changes(user, anor):
    product, materials = anor
    services.save_day(DAY, {product: 1000}, user=user)
    run = ProductionRun.objects.get()
    assert run.lines.get(material=materials["sugar"]).quantity_per_unit == D("0.04635")

    RecipeLine.objects.filter(material=materials["sugar"]).update(quantity_per_unit=D("1"))
    services.save_day(DAY, {product: 2000}, user=user)  # re-saved with another quantity
    sugar = run.lines.get(material=materials["sugar"])
    assert sugar.quantity_per_unit == D("0.04635")  # its own norm, not the changed recipe
    assert sugar.consumed == D("92.7000")
    assert -MaterialMove.objects.get(run=run, material=materials["sugar"]).quantity == D("92.7000")


def test_daily_preview_uses_the_production_s_own_recipe_and_defect(client, user, anor):
    product, materials = anor
    client.force_login(user)
    client.post(url(product), form_data(materials))
    needs = {need.material: need.needed for need in services.material_needs({product: 166680}, date=DAY)}
    assert needs[materials["sugar"]] == D("7919.9683")
    assert needs[materials["can"]] == D(166980)


def test_lines_can_be_removed_added_and_must_not_repeat(client, user, anor):
    product, materials = anor
    client.force_login(user)
    data = form_data(materials)
    data["lines-3-material"] = materials["can"].pk  # the tray line now repeats the can
    response = client.post(url(product), data)
    assert response.status_code == 200
    assert "Этот материал уже есть в калькуляции." in response.content.decode()
    assert not ProductionRun.objects.exists()

    data = form_data(materials)
    data["lines-1-DELETE"] = "on"  # no citric acid this time
    client.post(url(product), data)
    run = ProductionRun.objects.get()
    assert not run.lines.filter(material=materials["acid"]).exists()
    assert not product.recipe.filter(material=materials["acid"]).exists()


def test_brewed_defaults_to_the_finished_units(client, user, anor):
    product, materials = anor
    client.force_login(user)
    client.post(url(product), form_data(materials, quantity="1000", batch=""))
    run = ProductionRun.objects.get()
    assert (run.batch_quantity, run.brewed) == (None, 1000)
    assert run.lines.get(material=materials["sugar"]).consumed == D("47.5200")


def test_runs_recorded_before_get_the_recipe_copied(anor):
    product, materials = anor
    run = ProductionRun.objects.create(date=DAY, product=product, quantity=100, affects_stock=False)
    assert not RunLine.objects.exists()
    run.rebuild_moves()
    assert run.lines.count() == 4
    assert not MaterialMove.objects.filter(run=run).exists()  # history: stock unchanged


def test_viewers_cannot_save(client, planner, anor):
    product, materials = anor
    client.force_login(planner)
    if planner.has_perm("production.change_productionrun"):
        pytest.skip("the planner role may record production")
    assert client.post(url(product), form_data(materials)).status_code == 403
