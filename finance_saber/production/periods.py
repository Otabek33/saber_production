"""Closing a period: once a month is closed, its production records can't change.

Like 1C's «дата запрета изменения»: one date, ``closed until``, and nothing dated on or before it
can be added, changed, cancelled or deleted: production runs, material receipts, shipments and
stock movements (write-offs, counts), with their lines. Each closing (and reopening) is a new
ClosedPeriod row, so the history of who closed what, and who reopened it, stays.

Enforced in three places, so nothing slips through:
* forms with a date say so at once, next to the date (OpenPeriodFormMixin);
* every save and delete of those records is checked (``guard``, connected in apps.py), so an
  import, a cancel button or the admin site can't change a closed month either;
* a view that runs into it anyway shows the message instead of an error page
  (PeriodClosedMiddleware); with ATOMIC_REQUESTS nothing of that request is saved.

Closing: whoever has «Закрытие периода» (Finance, the Director). Reopening: the superuser only.
"""

import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete, pre_save
from django.shortcuts import redirect
from django.utils import formats
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _


class PeriodClosed(ValidationError):
    pass


def closed_until() -> datetime.date | None:
    """The last day that can't be changed, or None when nothing is closed."""
    from .models import ClosedPeriod

    latest = ClosedPeriod.objects.order_by("-created_at", "-pk").first()
    return latest.closed_until if latest else None


def is_closed(date: datetime.date | None, until: datetime.date | None = None) -> bool:
    until = until if until is not None else closed_until()
    return bool(date and until and date <= until)


def message(until: datetime.date) -> str:
    return _("The period up to %(date)s is closed: records dated then can't be added or changed.") % {
        "date": formats.date_format(until, "d.m.Y")
    }


def check(*dates) -> None:
    """Raise PeriodClosed if any of these dates is in the closed period."""
    until = closed_until()
    if until and any(is_closed(date, until) for date in dates):
        raise PeriodClosed(message(until), code="period_closed")


# --- Every save and delete ---------------------------------------------------------------------


def _dates(instance) -> list:
    """The record's date, and its date before this change (a date moved out of a closed month)."""
    from .models import MaterialReceiptLine, ProductShipmentLine, RunLine

    parents = {RunLine: "run", MaterialReceiptLine: "receipt", ProductShipmentLine: "shipment"}
    for model, attr in parents.items():
        if isinstance(instance, model):
            parent = getattr(instance, attr, None)
            return [parent.date] if parent is not None else []
    dates = [instance.date]
    if instance.pk:
        before = type(instance)._default_manager.filter(pk=instance.pk).values_list("date", flat=True).first()
        dates.append(before)
    return dates


def guard(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return  # loading fixtures
    check(*_dates(instance))


def connect():
    from .models import (
        MaterialMove,
        MaterialReceipt,
        MaterialReceiptLine,
        ProductionRun,
        ProductMove,
        ProductShipment,
        ProductShipmentLine,
        RunLine,
    )

    guarded = (
        ProductionRun, RunLine, MaterialReceipt, MaterialReceiptLine, ProductShipment, ProductShipmentLine,
        MaterialMove, ProductMove,
    )  # fmt: skip
    for model in guarded:
        pre_save.connect(guard, sender=model, dispatch_uid=f"periods.save.{model.__name__}")
        pre_delete.connect(guard, sender=model, dispatch_uid=f"periods.delete.{model.__name__}")


# --- Forms and views ---------------------------------------------------------------------------


class OpenPeriodFormMixin:
    """A form's ``date`` (and, when editing, the record's own date) must be in an open period."""

    def clean_date(self):
        date = self.cleaned_data.get("date")
        instance = getattr(self, "instance", None)
        original = getattr(instance, "date", None) if instance is not None and instance.pk else None
        check(date, original)
        return date


class PeriodClosedMiddleware:
    """Turn a closed-period refusal into a message on the page the user came from."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, PeriodClosed):
            return None
        messages.error(request, " ".join(exception.messages))
        back = request.META.get("HTTP_REFERER", "")
        if back and url_has_allowed_host_and_scheme(back, {request.get_host()}, require_https=request.is_secure()):
            return redirect(back)
        return redirect(request.path if request.method == "POST" else "production:close")


def months(until: datetime.date | None, today: datetime.date, back: int = 12) -> list[dict]:
    """The last ``back`` months, newest first, each with whether it is closed."""
    first = today.replace(day=1)
    result = []
    for _step in range(back):
        end = (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
        result.append({"month": first, "end": end, "closed": is_closed(end, until), "current": first <= today <= end})
        first = (first - datetime.timedelta(days=1)).replace(day=1)
    return result
