"""Control: closing a period, the supplier record, and production alerts in the daily report."""

import datetime
from decimal import Decimal

import pytest
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from finance_saber.cashbook.models import Company
from finance_saber.core import daily_report
from finance_saber.production import periods, services, suppliers
from finance_saber.production.models import (
    ClosedPeriod,
    Material,
    MaterialMove,
    MaterialOrder,
    MaterialReceipt,
    Product,
    ProductShipment,
)

D = Decimal
OCT = {n: datetime.date(2026, 10, n) for n in range(1, 32)}
NOV = {n: datetime.date(2026, 11, n) for n in range(1, 31)}


@pytest.fixture
def records(db):
    sugar = Material.objects.create(name="Сахар", unit="кг")
    tea = Product.objects.create(name="Чай", volume=D("0.45"))
    supplier = Company.objects.create(name="ООО Сахар", inn="1")
    customer = Company.objects.create(name="Магазин", inn="2")
    receipt = MaterialReceipt.objects.create(supplier=supplier, number="С-1", date=OCT[5])
    receipt.lines.create(material=sugar, quantity=D(1000), amount=D(10_000_000))
    receipt.rebuild_moves()
    run, _ = services.save_calculation(OCT[10], tea, 1000, None, [services.CalculationLine(sugar, D("0.001"))])
    shipment = ProductShipment.objects.create(date=OCT[20], customer=customer)
    shipment.assign_number()
    shipment.lines.create(product=tea, quantity=100, amount=D(1))
    shipment.rebuild_moves()
    return {"sugar": sugar, "tea": tea, "supplier": supplier, "receipt": receipt, "run": run, "shipment": shipment}


def close(until):
    return ClosedPeriod.objects.create(closed_until=until)


# --- Closing a period --------------------------------------------------------------------------------


def refused(action):
    """The change is refused; its own transaction, as a request's (ATOMIC_REQUESTS), is rolled back."""
    with pytest.raises(periods.PeriodClosed), transaction.atomic():
        action()


def test_nothing_in_a_closed_period_can_change(records):
    close(OCT[31])
    refused(lambda: services.save_calculation(OCT[10], records["tea"], 2000, None, []))
    refused(lambda: records["receipt"].lines.first().delete())
    refused(
        lambda: MaterialMove.objects.create(
            material=records["sugar"], date=OCT[31], kind=MaterialMove.Kind.WRITE_OFF, quantity=-1
        )
    )
    records["shipment"].is_cancelled = True
    refused(records["shipment"].save)
    assert records["run"].lines.count() == 1  # nothing changed


def test_a_record_cant_be_moved_out_of_a_closed_period_either(records):
    close(OCT[31])
    receipt = MaterialReceipt.objects.get(pk=records["receipt"].pk)
    receipt.date = NOV[2]
    refused(receipt.save)


def test_the_open_period_works_as_before(records):
    close(OCT[31])
    run, _ = services.save_calculation(NOV[2], records["tea"], 500, None, [])
    assert run.pk and run.date == NOV[2]


def test_a_form_says_so_at_the_date(client, technologist, records):
    close(OCT[31])
    client.force_login(technologist)
    data = {"date": "2026-10-15", "material": records["sugar"].pk, "quantity": "1", "note": "порча"}
    response = client.post(reverse("production:material_write_off"), data)
    assert "закрыт" in str(response.context["form"].errors["date"])


def test_a_view_running_into_it_shows_a_message_and_saves_nothing(client, technologist, records):
    close(OCT[31])
    client.force_login(technologist)
    url = reverse("production:receipt_cancel", args=[records["receipt"].pk])
    response = client.post(url, HTTP_REFERER="http://testserver/production/materials/receipts/", follow=True)
    assert response.redirect_chain[-1][0].endswith("/production/materials/receipts/")
    assert "закрыт" in response.content.decode()
    records["receipt"].refresh_from_db()
    assert not records["receipt"].is_cancelled


def test_closing_and_reopening(client, finance, superuser, records, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: NOV[4])
    client.force_login(finance)
    assert client.post(reverse("production:close_month"), {"month": "2026-11"}).status_code == 302
    assert periods.closed_until() is None  # November isn't over
    client.post(reverse("production:close_month"), {"month": "2026-10"})
    assert periods.closed_until() == OCT[31]
    assert client.post(reverse("production:reopen_month"), {"month": "2026-10", "note": "x"}).status_code == 403
    client.force_login(superuser)
    client.post(reverse("production:reopen_month"), {"month": "2026-10"})  # no reason: refused
    assert periods.closed_until() == OCT[31]
    client.post(reverse("production:reopen_month"), {"month": "2026-10", "note": "исправить отгрузку"})
    assert periods.closed_until() == datetime.date(2026, 9, 30)
    assert ClosedPeriod.objects.count() == 2  # the history stays


def test_production_roles_see_the_page_but_cant_close(client, technologist, records):
    client.force_login(technologist)
    assert client.get(reverse("production:close")).status_code == 200
    assert client.post(reverse("production:close_month"), {"month": "2026-10"}).status_code == 403


# --- Supplier record ---------------------------------------------------------------------------------


@pytest.fixture
def orders(records):
    """Two orders from the same supplier: one came on time, one 5 days late; a third is late now."""
    supplier, sugar = records["supplier"], records["sugar"]

    def order(number, expected, received_on=None):
        item = MaterialOrder.objects.create(
            supplier=supplier, number=number, order_date=OCT[1], expected_date=expected, currency="UZS"
        )
        line = item.lines.create(material=sugar, quantity=D(100))
        if received_on:
            receipt = MaterialReceipt.objects.create(
                supplier=supplier, number=f"R-{number}", date=received_on, order=item
            )
            receipt.lines.create(material=sugar, quantity=D(100), amount=D(1_100_000), order_line=line)
            receipt.rebuild_moves()
        return item

    order("1", OCT[10], received_on=OCT[9])
    order("2", OCT[10], received_on=OCT[15])
    order("3", OCT[20])
    return supplier


def test_deliveries_on_time_and_late(orders, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: NOV[4])
    [record] = suppliers.records(OCT[1], NOV[4])
    assert (record.receipts, record.on_time_percent, record.average_delay) == (3, 50, D(5))
    assert [line.order.number for line in record.late_now] == ["3"]


def test_prices_over_time(orders):
    [item] = suppliers.price_history(orders)
    # 10 000/kg on the 5th, then 11 000 twice.
    assert [point.price for point in item["points"]] == [D(10_000), D(11_000), D(11_000)]
    assert (item["last"], item["change"], item["points"][1].change) == (D(11_000), D("10.0"), D("10.0"))


def test_the_supplier_pages(client, technologist, orders):
    client.force_login(technologist)
    params = {"from": "2026-10-01", "to": "2026-11-04"}
    assert client.get(reverse("production:suppliers"), params).status_code == 200
    response = client.get(reverse("production:supplier", args=[orders.pk]), params)
    assert response.context["record"].on_time_percent == 50 and "+5" in response.content.decode()


# --- Alerts in the daily report ----------------------------------------------------------------------


def test_the_daily_report_has_production_alerts(technologist, orders, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: OCT[15])
    text = daily_report.render(technologist, OCT[15])
    assert "Производство: внимание" in text and "Поступило сегодня: 1" in text  # order 2's receipt
    assert "опаздывают" not in text  # order 3 is due on the 20th
    monkeypatch.setattr(timezone, "localdate", lambda *args: NOV[4])
    text = daily_report.render(technologist, NOV[4])
    assert "Заказы опаздывают: 1" in text and "Поступило сегодня" not in text


def test_no_alerts_no_block(technologist, db):
    assert "Производство: внимание" not in daily_report.render(technologist, OCT[15])
