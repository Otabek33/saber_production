"""The morning expiry notification: every day at 06:00, if finished-goods batches need checking at
the warehouse (expiry.alerts: 3, 2 or 1 months left, 2 weeks left, or expired), each user who may
see finished goods gets a notification in the header's bell (users.Notification).

``create_due`` makes them when a page shows the bell (users/context_processors.py): the first
page a user opens from 06:00 on brings that day's notification. ``manage.py expiry_notifications``
does it for everyone at once (from cron, if wanted, or to test). Each user gets at most one a day,
however often it runs. Nothing is sent outside the site.

Working out every batch in stock (traceability) is heavy, so the alerts are cached for a few
minutes and dropped when product stock moves (production, a shipment, a correction).
"""

import datetime

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models.signals import post_delete, post_save
from django.urls import reverse
from django.utils import timezone

from . import expiry

NOTIFY_AT = datetime.time(6, 0)  # local time (settings.TIME_ZONE)
KEEP_DAYS = 30  # older notifications are removed
SHOWN = 6  # batches kept in a notification; the rest are on the shelf-life page
CACHE_KEY = "production:expiry-alerts"
CACHE_SECONDS = 300


def _alert_rows(today: datetime.date) -> list[dict]:
    """The batches to check, as plain values (cached, and saved in the notification as JSON)."""
    return [
        {
            "product": lot.item.name,
            "code": lot.code,
            "run_pk": lot.run.pk if lot.run is not None else None,
            "best_before": lot.best_before.isoformat(),
            "days_left": lot.days_left,
            "remaining": str(lot.remaining),
            "level": lot.alert,
        }
        for lot in expiry.product_lots(today)
        if lot.alert
    ]


def product_alerts(today: datetime.date | None = None) -> list[dict]:
    today = today or timezone.localdate()
    cached = cache.get(CACHE_KEY)
    if cached is None or cached["day"] != today:
        cached = {"day": today, "rows": _alert_rows(today)}
        cache.set(CACHE_KEY, cached, CACHE_SECONDS)
    return cached["rows"]


def snapshot(rows: list[dict]) -> dict:
    """What a notification keeps: how many batches at each step, and the most urgent ones."""
    return {
        "count": len(rows),
        "urgent": sum(row["level"] in ("expired", "weeks_2") for row in rows),
        "levels": {key: sum(row["level"] == key for row in rows) for key, _label, _tone in expiry.ALERT_LEVELS},
        "items": rows[:SHOWN],
    }


def recipients(user=None):
    """Active users who may see finished goods (or just ``user``, if they may)."""
    from finance_saber.users.models import User

    users = [user] if user is not None else User.objects.filter(is_active=True)
    return [person for person in users if person.is_active and person.has_perm("production.view_product")]


def _now() -> datetime.datetime:
    """The local time now (its own function, so tests can set the clock)."""
    return timezone.localtime()


def create_due(now: datetime.datetime | None = None, user=None) -> int:
    """Make today's expiry notifications once it is 06:00, if anything needs checking; only for
    ``user`` when given (a page showing the bell). Returns how many were made."""
    from finance_saber.users.models import Notification

    now = timezone.localtime(now) if now else _now()
    if now.time() < NOTIFY_AT:
        return 0
    today = now.date()
    people = recipients(user)
    waiting = [
        person
        for person in people
        if not Notification.objects.filter(user=person, kind=Notification.Kind.EXPIRY, day=today).exists()
    ]
    if not waiting:
        return 0
    rows = product_alerts(today)
    if not rows:
        return 0
    data, url = snapshot(rows), f"{reverse('production:expiry')}?show=alerts"
    made = 0
    for person in waiting:
        try:
            with transaction.atomic():  # another run may make it at the same moment: one a day
                Notification.objects.create(user=person, kind=Notification.Kind.EXPIRY, day=today, data=data, url=url)
            made += 1
        except IntegrityError:
            pass
    if made:  # at most once a day per user: old ones go then
        Notification.objects.filter(day__lt=today - datetime.timedelta(days=KEEP_DAYS)).delete()
    return made


def forget(**kwargs):
    """Product stock moved: the alerts are worked out again. After the commit, so the stock written
    in the same transaction (moves rebuilt in bulk) is counted."""
    transaction.on_commit(lambda: cache.delete(CACHE_KEY))


def connect():
    from .models import Product, ProductionRun, ProductMove, ProductShipment

    # Moves change the stock (shipments and production write theirs in bulk, without signals of
    # their own, so those are watched too); a product's shelf life changes its dates.
    for model in (ProductMove, ProductionRun, ProductShipment, Product):
        post_save.connect(forget, sender=model, dispatch_uid=f"expiry-alerts-save-{model.__name__}")
        post_delete.connect(forget, sender=model, dispatch_uid=f"expiry-alerts-delete-{model.__name__}")
