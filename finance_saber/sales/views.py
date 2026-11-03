import datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_saber.core.permissions import permission

from . import services
from .forms import PlanUploadForm
from .models import Plan

MONTHS = range(1, 13)


@permission(view="sales.change_plan", change="sales.change_plan")
def import_plan(request):
    """Upload the yearly plan workbook (sales and production plan). The file is read in memory, not kept."""
    form = PlanUploadForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and form.is_valid():
        try:
            parsed = services.read_plan(form.cleaned_data["file"])
        except services.SheetError as error:
            form.add_error(None, str(error))
        else:
            report = services.import_plan(parsed, dry_run=form.cleaned_data["dry_run"])
            if report.saved:
                messages.success(request, _("Plan for %(year)s imported.") % {"year": report.year})
    return render(request, "sales/import.html", {"plan_form": form, "plan_report": report})


@permission(view="sales.view_plan", change="sales.change_plan")
def plan(request):
    """The year's plan by product and month next to what was actually sold (or produced)."""
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except ValueError:
        year = today.year
    kind = request.GET.get("kind") if request.GET.get("kind") in Plan.Kind.values else Plan.Kind.SALES
    if request.method == "POST":
        changed = save_plan_grid(request.POST, kind, year)
        messages.success(request, _("Plan saved (%(count)s figures changed).") % {"count": changed})
        return redirect(f"{reverse('sales:plan')}?year={year}&kind={kind}")
    rows, totals = services.plan_vs_fact(kind, year)
    for row in rows:
        row["cells"] = [
            {"month": month, "plan": plan_value, "fact": fact, "percent": percent(fact, plan_value)}
            for month, (plan_value, fact) in zip(MONTHS, row["months"], strict=True)
        ]
        row["percent"] = percent(row["fact"], row["plan"])
    plan_total, fact_total = sum(p for p, _f in totals), sum(f for _p, f in totals)
    return render(
        request,
        "sales/plan.html",
        {
            "year": year,
            "kind": kind,
            "kinds": Plan.Kind.choices,
            "rows": rows,
            "totals": [{"plan": p, "fact": f, "percent": percent(f, p)} for p, f in totals],
            "plan_total": plan_total,
            "fact_total": fact_total,
            "percent_total": percent(fact_total, plan_total),
            "months": [datetime.date(year, month, 1) for month in MONTHS],
            "current_month": today.month if today.year == year else None,
            "editing": "edit" in request.GET and request.user.has_perm("sales.change_plan"),
        },
    )


def percent(fact, plan_value):
    return round(fact * 100 / plan_value) if plan_value else None


def save_plan_grid(data, kind, year) -> int:
    """Write the edited plan cells (inputs named plan-<product id>-<month>)."""
    changed = 0
    existing = {(p.product_id, p.month): p for p in Plan.objects.filter(kind=kind, year=year)}
    for name, value in data.items():
        parts = name.split("-")
        if len(parts) != 3 or parts[0] != "plan" or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        product_id, month = int(parts[1]), int(parts[2])
        if month not in MONTHS:
            continue
        try:
            quantity = max(int(value.replace(" ", "").replace("\xa0", "") or 0), 0)
        except ValueError:
            continue
        current = existing.get((product_id, month))
        if current is None:
            if quantity:
                Plan.objects.create(kind=kind, year=year, month=month, product_id=product_id, quantity=quantity)
                changed += 1
        elif current.quantity != quantity:
            current.quantity = quantity
            current.save(update_fields=["quantity", "updated_at"])
            changed += 1
    return changed
