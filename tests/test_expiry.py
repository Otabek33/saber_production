"""Shelf life: batches and deliveries in stock, and until when each is good."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from finance_saber.cashbook.models import Company
from finance_saber.production import expiry, services
from finance_saber.production.models import Material, MaterialMove, MaterialReceipt, Product, ProductShipment

D = Decimal
TODAY = datetime.date(2026, 11, 4)


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("18 месяц", (18, "months")), ("24 месец", (24, "months")), ("12 мес.", (12, "months")),
        ("5 г", (5, "years")), ("2 года", (2, "years")), ("3 лет", (3, "years")), ("1,5 года", (18, "months")),
        ("180 дней", (180, "days")), ("6 нед", (6, "weeks")), ("12 months", (12, "months")),
        ("2 years", (2, "years")), ("12", None), ("", None), ("без срока", None),
    ],
)  # fmt: skip
def test_shelf_life_is_read_as_people_write_it(text, parsed):
    assert expiry.parse(text) == parsed


def test_best_before_adds_the_shelf_life():
    assert expiry.best_before(datetime.date(2026, 1, 31), "1 мес") == datetime.date(2026, 2, 28)
    assert expiry.best_before(datetime.date(2026, 1, 31), "2 года") == datetime.date(2028, 1, 31)
    assert expiry.best_before(datetime.date(2026, 1, 1), "30 дней") == datetime.date(2026, 1, 31)
    assert expiry.best_before(datetime.date(2026, 1, 1), "") is None


@pytest.fixture
def stock(db):
    """Tea (6 months): a batch on 1 May (expired) and one on 1 Sept (good until 1 March);
    shipments took the May batch only in part. Sugar (12 months): delivered 15 Nov 2025 (good until 15 Nov 2026)."""
    sugar = Material.objects.create(name="Сахар", unit="кг", shelf_life="12 месяцев")
    salt = Material.objects.create(name="Соль", unit="кг")  # no shelf life
    tea = Product.objects.create(name="Чай", volume=D("0.45"), shelf_life="6 месяцев")
    supplier = Company.objects.create(name="ООО Сахар", inn="1")
    receipt = MaterialReceipt.objects.create(supplier=supplier, number="С-1", date=datetime.date(2025, 11, 15))
    receipt.lines.create(material=sugar, quantity=D(1000))
    receipt.rebuild_moves()
    opening = MaterialMove.Kind.OPENING
    MaterialMove.objects.create(material=salt, date=datetime.date(2026, 1, 1), kind=opening, quantity=5)
    line = services.CalculationLine(sugar, D("0.001"))
    may, _ = services.save_calculation(datetime.date(2026, 5, 1), tea, 1000, None, [line])
    september, _ = services.save_calculation(datetime.date(2026, 9, 1), tea, 2000, None, [line])
    customer = Company.objects.create(name="Магазин", inn="2")
    shipment = ProductShipment.objects.create(date=datetime.date(2026, 10, 1), customer=customer)
    shipment.assign_number()
    shipment.lines.create(product=tea, quantity=600, amount=D(1))
    shipment.rebuild_moves()
    return {"tea": tea, "sugar": sugar, "salt": salt, "may": may, "september": september, "receipt": receipt}


def test_batches_in_stock_with_their_dates(stock):
    may, september = expiry.product_lots(TODAY)
    assert (may.run, may.remaining, may.best_before, may.status) == (
        stock["may"], 400, datetime.date(2026, 11, 1), expiry.Status.EXPIRED,
    )  # fmt: skip
    assert (september.run, september.remaining, september.days_left, september.status) == (
        stock["september"], 2000, 117, expiry.Status.GOOD,
    )  # fmt: skip
    assert expiry.summary([may, september]) == {
        "expired": 1, "expired_units": 400, "soon": 0, "soon_units": 0, "unknown_items": 0,
    }  # fmt: skip


def test_deliveries_in_stock_with_their_dates(stock):
    sugar, salt = expiry.material_lots(TODAY)
    assert (sugar.receipt, sugar.remaining, sugar.best_before) == (stock["receipt"], 997, datetime.date(2026, 11, 15))
    assert (sugar.days_left, sugar.status) == (11, expiry.Status.SOON)
    assert (salt.item, salt.best_before, salt.status) == (stock["salt"], None, expiry.Status.UNKNOWN)


def test_the_page_lists_what_needs_attention(client, technologist, stock):
    client.force_login(technologist)
    response = client.get(reverse("production:expiry"))
    goods, materials = response.context["groups"]
    assert [lot.run for lot in goods["lots"]] == [stock["may"]]  # only the expired one (by today's date)
    everything = client.get(reverse("production:expiry"), {"show": "all"}).context["groups"]
    assert len(everything[0]["lots"]) == 2 and len(everything[1]["lots"]) == 2
    assert "Сроки годности" in response.content.decode()


def test_a_material_takes_a_good_until_date_or_none(client, user, stock):
    client.force_login(user)
    url = reverse("production:material_edit", args=[stock["salt"].pk])
    data = {"name": "Соль", "unit": "кг", "kind": "raw", "min_stock": "0", "is_active": "on"}
    assert "best_before" in client.post(url, data | {"best_before": "завтра"}).context["form"].errors
    assert client.post(url, data | {"best_before": ""}).status_code == 302  # not required
    stock["salt"].refresh_from_db()
    assert stock["salt"].best_before is None
    assert client.post(url, data | {"best_before": "2026-12-01"}).status_code == 302
    stock["salt"].refresh_from_db()
    assert stock["salt"].best_before == datetime.date(2026, 12, 1)


def test_the_material_date_wins_over_the_text_shelf_life(stock):
    stock["sugar"].best_before = datetime.date(2026, 11, 20)  # its text says 12 months from receipt
    stock["sugar"].save()
    stock["salt"].best_before = datetime.date(2026, 11, 1)
    stock["salt"].save()
    salt, sugar = expiry.material_lots(TODAY)
    assert (salt.best_before, salt.status) == (datetime.date(2026, 11, 1), expiry.Status.EXPIRED)
    assert (sugar.best_before, sugar.days_left, sugar.status) == (datetime.date(2026, 11, 20), 16, expiry.Status.SOON)


def test_the_dashboard_tile_counts_expiring_batches(client, technologist, stock):
    client.force_login(technologist)
    response = client.get(reverse("production:index"))
    assert response.context["expiring"]["expired"] >= 1
    assert reverse("production:expiry") in response.content.decode()


# --- An opening balance really arrived earlier ------------------------------------------------------


def opening(material):
    return MaterialMove.objects.get(material=material, kind=MaterialMove.Kind.OPENING)


def test_the_arrival_day_of_an_opening_balance_can_be_given(client, technologist, stock):
    """Salt: 5 kg opening on 1 Jan 2026: with «6 месяцев», good until 1 July. It really came on 1 Oct 2025."""
    salt = stock["salt"]
    salt.shelf_life = "6 месяцев"
    salt.save()
    move = opening(salt)
    client.force_login(technologist)
    response = client.post(reverse("production:expiry_dates"), {f"received_{move.pk}": "2025-10-01", "show": "all"})
    assert response["Location"].endswith("?show=all#materials")
    move.refresh_from_db()
    assert (move.received_on, move.date) == (
        datetime.date(2025, 10, 1),
        datetime.date(2026, 1, 1),
    )  # the ledger's date stays
    [lot] = [lot for lot in expiry.material_lots(TODAY) if lot.item == salt]
    assert (lot.date, lot.best_before, lot.status) == (
        datetime.date(2025, 10, 1), datetime.date(2026, 4, 1), expiry.Status.EXPIRED,
    )  # fmt: skip


def test_a_future_day_or_a_receipt_is_left_alone(client, technologist, stock):
    client.force_login(technologist)
    move = opening(stock["salt"])
    receipt_move = MaterialMove.objects.get(material=stock["sugar"], kind=MaterialMove.Kind.RECEIPT)
    client.post(
        reverse("production:expiry_dates"),
        {f"received_{move.pk}": "2030-01-01", f"received_{receipt_move.pk}": "2025-01-01"},
    )
    move.refresh_from_db()
    receipt_move.refresh_from_db()
    assert move.received_on is None and receipt_move.received_on is None


def test_the_day_can_be_given_in_a_closed_period_and_needs_the_right_to_change_stock(
    client, technologist, finance, stock
):
    from finance_saber.production.models import ClosedPeriod

    move = opening(stock["salt"])
    ClosedPeriod.objects.create(closed_until=datetime.date(2026, 10, 31))  # the opening balance is in it
    client.force_login(finance)  # views production, doesn't change stock
    assert client.post(reverse("production:expiry_dates"), {f"received_{move.pk}": "2025-10-01"}).status_code == 403
    client.force_login(technologist)
    client.post(reverse("production:expiry_dates"), {f"received_{move.pk}": "2025-10-01"})
    move.refresh_from_db()
    assert move.received_on == datetime.date(2025, 10, 1)  # not an accounting change: allowed


def test_the_page_offers_the_day_for_opening_balances_only(client, technologist, stock):
    client.force_login(technologist)
    content = client.get(reverse("production:expiry"), {"show": "all"}).content.decode()
    assert f'name="received_{opening(stock["salt"]).pk}"' in content
    receipt_move = MaterialMove.objects.get(material=stock["sugar"], kind=MaterialMove.Kind.RECEIPT)
    assert f'name="received_{receipt_move.pk}"' not in content
