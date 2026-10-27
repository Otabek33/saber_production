import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.models import ExchangeRate
from finance_saber.core.permissions import permission

from . import services
from .forms import ExchangeRateForm

PLAN_EDIT = ("add", "change", "delete")


def selected_week(request) -> list[datetime.date]:
    return services.week_of(parse_date(request.GET.get("date") or "") or timezone.localdate())


def week_context(request, days):
    return {
        "days": days,
        "today": timezone.localdate(),
        "previous_week": days[0] - datetime.timedelta(days=7),
        "next_week": days[0] + datetime.timedelta(days=7),
        "editing": "edit" in request.GET,
    }


@permission(view="planning.view_cashplan", change=tuple(f"planning.{a}_cashplan" for a in PLAN_EDIT))
def cash_plan(request):
    """The week's payment calendar: planned money in and out by article, next to the cash book."""
    days = selected_week(request)
    if request.method == "POST":
        changed = services.save_cash_plan(request.POST, days, user=request.user)
        messages.success(request, _("Plan saved (%(count)s figures changed).") % {"count": changed})
        return redirect(f"{reverse('planning:cash')}?date={days[0].isoformat()}")
    rate = ExchangeRate.on(days[-1])
    sections, totals = services.cash_plan_grid(days, rate=rate)
    net = [
        {"plan": income["plan"] - expense["plan"], "fact": income["fact"] - expense["fact"]}
        for income, expense in zip(totals["income"]["days"], totals["expense"]["days"], strict=True)
    ]
    return render(
        request,
        "planning/cash_plan.html",
        {
            **week_context(request, days),
            "income_rows": sections["income"],
            "expense_rows": sections["expense"],
            "totals": totals,
            "net": net,
            "rate": rate,
        },
    )


@permission(view="planning.view_collectionplan", change=tuple(f"planning.{a}_collectionplan" for a in PLAN_EDIT))
def collection_plan(request):
    """The week's planned collections by sales region, next to bank receipts from each region."""
    days = selected_week(request)
    if request.method == "POST":
        changed = services.save_collection_plan(request.POST, days, user=request.user)
        messages.success(request, _("Plan saved (%(count)s figures changed).") % {"count": changed})
        return redirect(f"{reverse('planning:collections')}?date={days[0].isoformat()}")
    rows, totals = services.collection_grid(days)
    plan_total = sum((row["plan"] for row in rows), services.ZERO)
    fact_total = sum((row["fact"] for row in rows), services.ZERO)
    return render(
        request,
        "planning/collection_plan.html",
        {
            **week_context(request, days),
            "rows": rows,
            "totals": totals,
            "plan_total": plan_total,
            "fact_total": fact_total,
            "percent_total": services.percent(fact_total, plan_total),
        },
    )


@permission(view="cashbook.view_exchangerate", change="cashbook.add_exchangerate")
def exchange_rates(request):
    """USD rates by date: add the day's rate; the latest one on or before a date applies to it."""
    form = ExchangeRateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rate, created = ExchangeRate.objects.update_or_create(
            date=form.cleaned_data["date"], defaults={"rate": form.cleaned_data["rate"]}
        )
        messages.success(request, _("Rate saved.") if created else _("Rate updated."))
        return redirect("planning:rates")
    page = Paginator(ExchangeRate.objects.all(), 20).get_page(request.GET.get("page"))
    return render(request, "planning/exchange_rates.html", {"form": form, "page_obj": page})
