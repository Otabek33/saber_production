"""Production status: a draft moves no stock and can be deleted; posting moves stock; posted is cancelled."""

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
DAILY = reverse("production:daily")
Status = ProductionRun.Status


@pytest.fixture
def tea(db):
    sugar = Material.objects.create(name="Сахар", unit="кг")
    MaterialMove.objects.create(material=sugar, date=DAY, kind=MaterialMove.Kind.OPENING, quantity=D(100))
    product = Product.objects.create(name="Чай", volume=D("0.45"))
    RecipeLine.objects.create(product=product, material=sugar, quantity_per_unit=D("0.01"))
    return product, sugar


def post_day(client, product, quantity, button="post", **extra):
    data = {"date": DAY.isoformat(), f"product_{product.pk}": str(quantity), button: "1", **extra}
    return client.post(f"{DAILY}?date={DAY.isoformat()}", data)


def run_of(product):
    return ProductionRun.objects.filter(date=DAY, product=product).first()


# --- A draft changes nothing; posting does -----------------------------------------------------


def test_a_draft_keeps_the_quantities_but_moves_no_stock(client, technologist, tea):
    product, sugar = tea
    client.force_login(technologist)
    assert post_day(client, product, 1000, button="draft").status_code == 302
    run = run_of(product)
    assert (run.status, run.quantity) == (Status.DRAFT, 1000)
    assert run.lines.get().consumed == D(10)  # what it would use is known...
    assert not MaterialMove.objects.filter(run=run).exists()  # ...but nothing is written off
    assert not ProductMove.objects.filter(run=run).exists()
    assert material_stock()[sugar.pk] == D(100)
    assert product_stock().get(product.pk, 0) == 0


def test_posting_writes_off_materials_and_adds_the_output(client, technologist, tea):
    product, sugar = tea
    client.force_login(technologist)
    post_day(client, product, 1000, button="draft")
    post_day(client, product, 1000)  # the same quantities, now posted
    assert run_of(product).status == Status.POSTED
    assert material_stock()[sugar.pk] == D(90)
    assert product_stock()[product.pk] == 1000


def test_a_draft_needs_no_stock_but_posting_warns_about_a_shortage(client, technologist, tea):
    product, sugar = tea
    client.force_login(technologist)
    assert post_day(client, product, 50_000, button="draft").status_code == 302  # 500 kg of 100: still a draft
    response = post_day(client, product, 50_000)
    assert response.status_code == 200  # posting it shows the shortage first
    assert run_of(product).status == Status.DRAFT
    assert post_day(client, product, 50_000, button="confirm").status_code == 302
    assert run_of(product).status == Status.POSTED
    assert material_stock()[sugar.pk] == D(-400)


def test_saving_a_draft_over_posted_production_unposts_it(client, technologist, tea):
    product, sugar = tea
    client.force_login(technologist)
    post_day(client, product, 1000)
    page = client.get(f"{DAILY}?date={DAY.isoformat()}").content.decode()
    assert "data-confirm" in page  # the draft button asks first: it returns materials to stock
    post_day(client, product, 1000, button="draft")
    assert run_of(product).status == Status.DRAFT
    assert material_stock()[sugar.pk] == D(100)


# --- Deleting and cancelling ---------------------------------------------------------------------


def test_a_draft_can_be_deleted(client, technologist, tea):
    product, _sugar = tea
    client.force_login(technologist)
    post_day(client, product, 1000, button="draft")
    response = client.post(reverse("production:run_delete", args=[run_of(product).pk]))
    assert response.status_code == 302
    assert run_of(product) is None


def test_posted_production_is_not_deleted(client, technologist, tea):
    product, _sugar = tea
    client.force_login(technologist)
    post_day(client, product, 1000)
    client.post(reverse("production:run_delete", args=[run_of(product).pk]))
    assert run_of(product).status == Status.POSTED  # still there: posted production is cancelled instead


def test_zero_deletes_a_draft_and_cancels_posted_production(client, technologist, tea):
    product, sugar = tea
    other = Product.objects.create(name="Сок", volume=D("1"))
    client.force_login(technologist)
    services.save_day(DAY, {product: 1000}, post=True)
    services.save_day(DAY, {other: 50}, post=False)
    services.save_day(DAY, {product: 0, other: 0})
    assert run_of(product).status == Status.CANCELLED  # kept: it was posted
    assert run_of(other) is None  # gone: it was only a draft
    assert material_stock()[sugar.pk] == D(100)


def test_only_those_who_record_production_delete_drafts(client, finance, tea):
    product, _sugar = tea
    services.save_day(DAY, {product: 1000}, post=False)
    client.force_login(finance)  # views production only
    assert client.post(reverse("production:run_delete", args=[run_of(product).pk])).status_code == 403
    assert run_of(product) is not None


# --- Drafts count nowhere ------------------------------------------------------------------------


def test_drafts_count_in_no_report(client, technologist, tea):
    product, _sugar = tea
    services.save_day(DAY, {product: 1000}, post=False)
    client.force_login(technologist)
    journal = client.get(reverse("production:journal"), {"month": "2026-10"})
    assert journal.status_code == 200
    assert "1 000" not in journal.content.decode()
    assert client.get(reverse("production:batches")).context["page_obj"].paginator.count == 0
    services.save_day(DAY, {product: 1000}, post=True)
    assert client.get(reverse("production:batches")).context["page_obj"].paginator.count == 1


def test_daily_page_shows_each_products_status(client, technologist, tea):
    product, _sugar = tea
    other = Product.objects.create(name="Сок", volume=D("1"))
    services.save_day(DAY, {product: 1000}, post=True)
    services.save_day(DAY, {other: 50}, post=False)
    client.force_login(technologist)
    page = client.get(f"{DAILY}?date={DAY.isoformat()}").content.decode()
    assert "Черновик" in page and "Проведено" in page
    assert "1 продукт этого дня — черновик" in page
    assert reverse("production:run_delete", args=[run_of(other).pk]) in page
    assert reverse("production:run_delete", args=[run_of(product).pk]) not in page  # posted: no delete


# --- The calculation page --------------------------------------------------------------------------


def calculation(client, product, sugar, button):
    url = reverse("production:calculation", kwargs={"date": DAY.isoformat(), "product_pk": product.pk})
    data = {
        "quantity": "1000", "batch_quantity": "", button: "1",
        "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-material": sugar.pk, "lines-0-norm": "10", "lines-0-defect": "0", "lines-0-allowed_loss": "2",
    }  # fmt: skip
    return client.post(url, data)


def test_calculation_can_be_saved_as_a_draft_then_posted(client, technologist, tea):
    product, sugar = tea
    client.force_login(technologist)
    assert calculation(client, product, sugar, "draft").status_code == 302
    assert run_of(product).status == Status.DRAFT
    assert material_stock()[sugar.pk] == D(100)
    assert calculation(client, product, sugar, "post").status_code == 302
    assert run_of(product).status == Status.POSTED
    assert material_stock()[sugar.pk] < D(100)
