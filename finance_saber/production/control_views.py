"""Control pages: closing a period, and the supplier record."""

import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import formats, timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from finance_saber.cashbook.models import Company
from finance_saber.core.permissions import permission

from . import periods, suppliers
from .models import ClosedPeriod


@permission(view="production.view_closedperiod")
def close_page(request):
    """Which months are closed, closing the next ones, and (superuser) reopening; with the history."""
    today = timezone.localdate()
    until = periods.closed_until()
    return render(
        request,
        "production/period_close.html",
        {
            "until": until,
            "months": periods.months(until, today),
            "history": ClosedPeriod.objects.select_related("created_by")[:20],
            "today": today,
        },
    )


def _month_end(text: str) -> tuple[datetime.date, datetime.date] | None:
    first = parse_date(f"{text}-01") if text else None
    if first is None:
        return None
    return first, (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)


@require_POST
@permission(view="production.view_closedperiod", change="production.add_closedperiod")
def close_month(request):
    """Close everything up to the end of a finished month."""
    span = _month_end(request.POST.get("month", ""))
    until = periods.closed_until()
    if span is None:
        messages.error(request, _("Choose a month."))
    elif span[1] >= timezone.localdate():
        messages.error(request, _("A month can be closed once it is over."))
    elif until and span[1] <= until:
        messages.info(request, _("That month is already closed."))
    else:
        ClosedPeriod.objects.create(
            closed_until=span[1], note=request.POST.get("note", "")[:255], created_by=request.user
        )
        messages.success(request, _("Closed up to %(date)s.") % {"date": formats.date_format(span[1], "d.m.Y")})
    return redirect("production:close")


@require_POST
@permission(view="production.view_closedperiod")
def reopen_month(request):
    """The superuser reopens a month (and every month after it): the closed date moves back before it."""
    if not request.user.is_superuser:
        raise PermissionDenied
    span = _month_end(request.POST.get("month", ""))
    until = periods.closed_until()
    note = request.POST.get("note", "").strip()
    if span is None or not until or span[0] > until:
        messages.info(request, _("That month is not closed."))
    elif not note:
        messages.error(request, _("Say why the month is reopened."))
    else:
        new_until = span[0] - datetime.timedelta(days=1)
        ClosedPeriod.objects.create(closed_until=new_until, note=note[:255], created_by=request.user)
        messages.success(
            request,
            _("Reopened from %(date)s. Close it again once it is corrected.")
            % {"date": formats.date_format(span[0], "d.m.Y")},
        )
    return redirect("production:close")


def _period(request) -> tuple[datetime.date, datetime.date]:
    today = timezone.localdate()
    start = parse_date(request.GET.get("from") or "") or today.replace(month=1, day=1)
    end = parse_date(request.GET.get("to") or "") or today
    return start, end


@permission(view="production.view_materialreceipt")
def suppliers_page(request):
    """Every supplier: deliveries in the period, on time or late, and what was bought from them."""
    start, end = _period(request)
    return render(
        request,
        "production/suppliers.html",
        {"records": suppliers.records(start, end), "start": start, "end": end},
    )


@permission(view="production.view_materialreceipt")
def supplier_page(request, pk):
    """One supplier: its deliveries against the expected dates, what is late now, and its prices over time."""
    supplier = get_object_or_404(Company, pk=pk)
    start, end = _period(request)
    return render(
        request,
        "production/supplier_detail.html",
        {
            "supplier": supplier,
            "record": suppliers.deliveries(supplier, start, end),
            "prices": suppliers.price_history(supplier),
            "start": start,
            "end": end,
        },
    )
