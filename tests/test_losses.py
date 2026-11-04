"""Norms against actual use: defects and their reasons, write-offs, stock-count shortages, against the allowed loss."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.urls import reverse

from finance_saber.production import losses, services
from finance_saber.production.forms import CalculationLineForm
from finance_saber.production.models import Material, MaterialMove, Product, RunLine

D = Decimal
DAY = {n: datetime.date(2026, 10, n) for n in range(1, 31)}
REASON = RunLine.DefectReason


def produce(product, date, units, lines):
    run, _updated = services.save_calculation(date, product, units, None, lines)
    return run


@pytest.fixture
def month(db):
    """Sugar: 2 runs use 100 kg by the norm (2 % allowed), 3 kg defect; a 1 kg write-off; a count finds 4 kg short.
    Cans: 1000 by the norm, 5 scrapped (faulty) — under the 2 % allowed."""
    sugar = Material.objects.create(name="Сахар", unit="кг", price=D(10_000))
    cans = Material.objects.create(name="Банка", unit="шт", kind=Material.Kind.PACKAGING)
    drink = Product.objects.create(name="Чай", volume=D("0.45"))
    for material, quantity in ((sugar, 500), (cans, 5000)):
        MaterialMove.objects.create(material=material, date=DAY[1], kind=MaterialMove.Kind.OPENING, quantity=quantity)
    line = services.CalculationLine
    produce(drink, DAY[2], 500, [
        line(sugar, D("0.1"), defect=D(2), defect_reason=REASON.SPILLAGE),
        line(cans, D(1), defect=D(5), defect_reason=REASON.PACKAGING),
    ])  # fmt: skip
    produce(drink, DAY[3], 500, [line(sugar, D("0.1"), defect=D(1), defect_reason=REASON.SPILLAGE), line(cans, D(1))])
    MaterialMove.objects.create(material=sugar, date=DAY[5], kind=MaterialMove.Kind.WRITE_OFF, quantity=D(-1))
    MaterialMove.objects.create(material=sugar, date=DAY[6], kind=MaterialMove.Kind.ADJUSTMENT, quantity=D(-4))
    return {"sugar": sugar, "cans": cans, "drink": drink}


def test_losses_per_material_against_the_allowed(month):
    report = losses.report(DAY[1], DAY[30])
    sugar, cans = report.materials  # over the allowed first
    assert sugar.material == month["sugar"]
    assert (sugar.norm, sugar.allowed, sugar.defect, sugar.write_off, sugar.shortage) == (100, 2, 3, 1, 4)
    assert (sugar.losses, sugar.actual, sugar.percent, sugar.allowed_percent) == (8, 108, D("8.0"), D("2.0"))
    assert sugar.too_much and sugar.over_allowed == 6
    assert (sugar.losses_value, sugar.over_allowed_value) == (D(80_000), D(60_000))
    assert (cans.norm, cans.defect, cans.percent, cans.too_much) == (1000, 5, D("0.5"), False)
    assert cans.losses_value is None  # no price
    assert report.runs == 2 and report.unpriced == 1


def test_a_count_surplus_counts_against_shortages(month):
    MaterialMove.objects.create(material=month["sugar"], date=DAY[7], kind=MaterialMove.Kind.ADJUSTMENT, quantity=10)
    [sugar] = [row for row in losses.report(DAY[1], DAY[30]).materials if row.material == month["sugar"]]
    assert sugar.shortage == -6 and sugar.losses == 4  # the surplus doesn't make losses negative


def test_defects_are_added_up_by_reason(month):
    spillage, packaging = losses.report(DAY[1], DAY[30]).reasons
    assert (spillage.reason, spillage.entries, spillage.value) == (REASON.SPILLAGE, 2, D(30_000))
    assert spillage.top == [(month["sugar"], D(3))]
    assert (packaging.reason, packaging.entries, packaging.value, packaging.unpriced) == (REASON.PACKAGING, 1, 0, 1)


def test_a_defect_needs_its_reason(month):
    data = {"material": month["sugar"].pk, "norm": "100", "defect": "2", "allowed_loss": "2"}
    form = CalculationLineForm(data)
    assert not form.is_valid() and "defect_reason" in form.errors
    assert CalculationLineForm(data | {"defect_reason": REASON.EQUIPMENT}).is_valid()
    assert CalculationLineForm(data | {"defect": ""}).is_valid()  # no defect: no reason needed


def test_the_reason_is_kept_with_the_run(month):
    run = produce(month["drink"], DAY[10], 100, [services.CalculationLine(month["sugar"], D("0.1"), defect=D(0))])
    assert run.lines.get().defect_reason == ""
    [line] = month["drink"].runs.get(date=DAY[2]).lines.filter(material=month["sugar"])
    assert line.defect_reason == REASON.SPILLAGE


def test_the_page_shows_money_only_to_those_who_may_see_costs(client, technologist, finance, month):
    url = reverse("production:losses")
    client.force_login(technologist)
    response = client.get(url, {"month": "2026-10"})
    assert response.status_code == 200 and not response.context["show_money"]
    content = response.content.decode()
    assert "Разлив, протечка" in content and "Сверх допуска, сум" not in content
    client.force_login(finance)
    response = client.get(url, {"month": "2026-10"})
    assert response.context["show_money"] and "Сверх допуска, сум" in response.content.decode()


def test_the_excel_has_materials_and_reasons(client, finance, month):
    client.force_login(finance)
    response = client.get(reverse("production:losses"), {"month": "2026-10", "export": "xlsx"})
    book = openpyxl.load_workbook(BytesIO(response.content))
    losses_sheet, reasons_sheet = book.worksheets
    assert [cell.value for cell in losses_sheet[2]][:8] == ["Сахар", "кг", 100, 2, 3, 1, 4, 8]
    assert [cell.value for cell in reasons_sheet[2]] == ["Разлив, протечка", 2, 30_000]
