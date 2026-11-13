"""Expiry alerts: batches flagged 3, 2 and 1 months before they go off, then 2 weeks before;
shown in the header's notifications (each morning from 06:00), on the production overview and the shelf-life page."""

import datetime
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from finance_saber.production import expiry, notifications, services
from finance_saber.production.models import Material, Product
from finance_saber.users.models import Notification

D = Decimal
TODAY = datetime.date(2026, 11, 13)


@pytest.mark.parametrize(
    ("best_before", "level"),
    [
        (datetime.date(2026, 11, 12), "expired"),
        (datetime.date(2026, 11, 13), "weeks_2"),  # the last good day is today
        (datetime.date(2026, 11, 27), "weeks_2"),  # 14 days
        (datetime.date(2026, 11, 28), "month_1"),
        (datetime.date(2026, 12, 13), "month_1"),
        (datetime.date(2026, 12, 14), "months_2"),
        (datetime.date(2027, 1, 13), "months_2"),
        (datetime.date(2027, 2, 13), "months_3"),
        (datetime.date(2027, 2, 14), None),
        (None, None),
    ],
)
def test_alert_steps(best_before, level):
    assert expiry.alert_level(best_before, TODAY) == level


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def batches(db):
    """Tea, 12 months: one batch made 11 months and a week ago (about 3 weeks left), one made today."""
    today = timezone.localdate()
    sugar = Material.objects.create(name="Сахар", unit="кг")
    tea = Product.objects.create(name="Чай", volume=D("0.45"), shelf_life="12 месяцев")
    line = services.CalculationLine(sugar, D("0.001"))
    old_day = expiry.add_months(today, -12) + datetime.timedelta(days=21)
    old, _ = services.save_calculation(old_day, tea, 500, None, [line])
    new, _ = services.save_calculation(today, tea, 800, None, [line])
    return {"tea": tea, "old": old, "new": new, "today": today}


def test_alerts_list_only_batches_within_3_months(batches):
    found = expiry.alerts(expiry.product_lots(batches["today"]))
    assert [lot.run for lot in found["lots"]] == [batches["old"]]
    assert {level["key"]: level["count"] for level in found["levels"]}["month_1"] == 1
    assert found["urgent"] == 0


def at(hour: int, minute: int = 0) -> datetime.datetime:
    """Today at this local time."""
    return timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(hour, minute)))


def expiry_notes(user):
    return Notification.objects.filter(user=user, kind=Notification.Kind.EXPIRY)


def test_the_morning_notification_comes_at_6_once_a_day(technologist, batches):
    assert notifications.create_due(at(5, 59)) == 0
    assert notifications.create_due(at(6, 0)) >= 1
    assert notifications.create_due(at(6, 5)) == 0  # the timer runs again: nothing new
    [note] = expiry_notes(technologist)
    assert (note.day, note.data["count"], note.data["levels"]["month_1"]) == (batches["today"], 1, 1)
    assert note.url == reverse("production:expiry") + "?show=alerts"
    assert note.data["items"][0]["run_pk"] == batches["old"].pk


def test_no_notification_when_nothing_needs_checking(technologist, db):
    assert notifications.create_due(at(7)) == 0
    assert not expiry_notes(technologist).exists()


def test_no_notification_without_access_to_finished_goods(django_user_model, batches):
    guest = django_user_model.objects.create_user("guest", password="x")
    notifications.create_due(at(7))
    assert not expiry_notes(guest).exists()


def test_the_bell_shows_unread_notifications_beside_the_profile(client, technologist, batches):
    notifications.create_due(at(7))
    client.force_login(technologist)
    page = client.get(reverse("core:home")).content.decode()
    note = expiry_notes(technologist).get()
    assert 'class="notify-badge' in page
    assert reverse("users:notification_open", args=[note.pk]) in page
    assert page.index('id="notificationsDropdown"') < page.index('id="profileDropdown"')


def test_opening_a_notification_marks_it_read(client, technologist, batches):
    notifications.create_due(at(7))
    note = expiry_notes(technologist).get()
    client.force_login(technologist)
    response = client.post(reverse("users:notification_open", args=[note.pk]))
    assert response.url == note.url
    note.refresh_from_db()
    assert note.is_read
    assert 'class="notify-badge' not in client.get(reverse("core:home")).content.decode()


def test_mark_all_read(client, technologist, batches):
    notifications.create_due(at(7))
    client.force_login(technologist)
    response = client.post(reverse("users:notifications_read"), {"next": "https://evil.example/"})
    assert response.url == reverse("core:home")  # never back to another site
    assert not expiry_notes(technologist).filter(read_at__isnull=True).exists()


def test_someone_elses_notification_cant_be_opened(client, technologist, planner, batches):
    notifications.create_due(at(7))
    note = expiry_notes(technologist).get()
    client.force_login(planner)
    assert client.post(reverse("users:notification_open", args=[note.pk])).status_code == 404


def test_the_alerts_are_worked_out_again_when_a_product_changes(batches, django_capture_on_commit_callbacks):
    assert len(notifications.product_alerts()) == 1
    with django_capture_on_commit_callbacks(execute=True):
        batches["tea"].shelf_life = "24 месяца"  # the old batch now has a year left: no alert
        batches["tea"].save()
    assert cache.get(notifications.CACHE_KEY) is None
    assert notifications.product_alerts() == []


def test_the_overview_has_the_shelf_life_card(client, technologist, batches):
    client.force_login(technologist)
    response = client.get(reverse("production:index"))
    assert response.context["shelf_alerts"]["count"] == 1
    assert "shelf-levels" in response.content.decode()


def test_the_shelf_life_page_filters_the_alerts(client, technologist, batches):
    client.force_login(technologist)
    response = client.get(reverse("production:expiry"), {"show": "alerts"})
    [goods, *_rest] = response.context["groups"]
    assert [lot.run for lot in goods["lots"]] == [batches["old"]]


def test_the_sample_command_puts_a_notification_in_the_bell(technologist, db):
    from django.core.management import call_command

    call_command("expiry_notifications", sample=True, user=technologist.username)
    call_command("expiry_notifications", sample=True, user=technologist.username)  # replaces, never doubles
    note = expiry_notes(technologist).get()
    assert note.data["sample"] and note.data["count"] == 5 and note.data["urgent"] == 2


def test_notifications_older_than_30_days_are_removed(technologist, batches):
    old = Notification.objects.create(
        user=technologist, kind=Notification.Kind.EXPIRY, day=batches["today"] - datetime.timedelta(days=31)
    )
    notifications.create_due(at(7))
    assert not Notification.objects.filter(pk=old.pk).exists()


def test_the_first_page_after_6_brings_the_days_notification(client, technologist, batches, monkeypatch):
    monkeypatch.setattr(notifications, "_now", lambda: at(6, 30))
    client.force_login(technologist)
    page = client.get(reverse("core:home")).content.decode()
    assert expiry_notes(technologist).filter(day=batches["today"]).exists()
    assert 'class="notify-badge' in page


def test_the_products_page_lists_each_batch_with_its_date(client, technologist, batches):
    client.force_login(technologist)
    response = client.get(reverse("production:products"))
    [row] = [row for row in response.context["rows"] if row["product"] == batches["tea"]]
    assert [lot.run for lot in row["lots"]] == [batches["old"], batches["new"]]
    assert row["first"].alert == "month_1"
    page = response.content.decode()
    assert f'id="lots-{batches["tea"].pk}"' in page
    assert row["first"].best_before.strftime("%d.%m.%Y") in page
    assert response.context["expiry"]["alerts"] == 1


def test_the_products_page_filters_by_shelf_life(client, technologist, batches):
    Product.objects.create(name="Вода", volume=D("1"), shelf_life="12 месяцев")  # nothing in stock
    client.force_login(technologist)
    alerts = client.get(reverse("production:products"), {"expiry": "alerts"}).context["rows"]
    assert [row["product"] for row in alerts] == [batches["tea"]]
    assert client.get(reverse("production:products"), {"expiry": "expired"}).context["rows"] == []
