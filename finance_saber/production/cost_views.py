"""Production cost, margin and losses pages (costing.py and losses.py work them out)."""

import datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from finance_saber.cashbook import exports
from finance_saber.core.permissions import permission

from . import costing, expiry
from . import losses as loss_report
from .models import MaterialMove, ProductShipment

COSTS = "production.view_costs"
GROUPINGS = ("product", "customer", "month")


def _month(request) -> datetime.date:
    return parse_date((request.GET.get("month") or "") + "-01") or timezone.localdate().replace(day=1)


def _month_end(month: datetime.date) -> datetime.date:
    return (month + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)


@permission(view=COSTS)
def costs(request):
    """What each product cost to make in a month, per unit, against the month before; one product's make-up."""
    month = _month(request)
    previous = (month - datetime.timedelta(days=1)).replace(day=1)
    rows = costing.product_costs(month, _month_end(month), previous)
    selected = next((row for row in rows if str(row.product.pk) == request.GET.get("product")), None)
    if selected is None and rows:
        selected = rows[0]
    missing = {material.pk: material for row in rows for material in row.missing}
    return render(
        request,
        "production/costs.html",
        {
            "month": month,
            "previous_month": previous,
            "next_month": (month + datetime.timedelta(days=32)).replace(day=1),
            "rows": rows,
            "selected": selected,
            "selected_materials": selected.materials() if selected else [],
            "missing": sorted(missing.values(), key=lambda material: material.name),
            "total_units": sum(row.units for row in rows),
            "total_amount": sum((row.amount for row in rows), costing.ZERO),
            "estimated": sum((row.estimated_amount for row in rows), costing.ZERO),
        },
    )


@permission(view=COSTS)
def margin(request):
    """Shipments against what they cost to make: by product, customer or month, for a period."""
    today = timezone.localdate()
    start = parse_date(request.GET.get("from") or "") or today.replace(day=1)
    end = parse_date(request.GET.get("to") or "") or today
    by = request.GET.get("by") if request.GET.get("by") in GROUPINGS else "product"
    shipments = ProductShipment.objects.posted().filter(date__gte=start, date__lte=end)
    rows = costing.group_margins(costing.shipment_margins(shipments), by)
    totals = costing.total(rows)
    if request.GET.get("export") == "xlsx":
        return _margin_xlsx(rows, totals, by, start, end)
    return render(
        request,
        "production/margin.html",
        {"rows": rows, "totals": totals, "by": by, "start": start, "end": end, "groupings": _groupings()},
    )


def _groupings():
    return [("product", _("By product")), ("customer", _("By customer")), ("month", _("By month"))]


def _margin_xlsx(rows, totals, by, start, end):
    first = {"product": _("Product"), "customer": _("Customer"), "month": _("Month")}[by]
    headers = [first, _("Units"), _("Shipped for"), _("Cost"), _("Margin"), _("Margin, %"), _("Of the cost estimated")]

    def values(label, row):
        return [label, row.units, costing.money(row.revenue), costing.money(row.cost), costing.money(row.margin),
                row.percent, costing.money(row.estimated)]  # fmt: skip

    lines = [values(row.label.strftime("%m.%Y") if by == "month" else row.label, row) for row in rows]
    lines.append(values(_("Total"), totals))
    sheet = exports.Sheet(_("Margin"), headers, lines, money_columns={2, 3, 4, 6})
    return exports.xlsx_response(f"margin-{start:%Y%m%d}-{end:%Y%m%d}.xlsx", [sheet])


@permission(view="production.view_materialmove")
def losses(request):
    """Norms against actual use for a month: defects (and why), write-offs and stock-count shortages."""
    month = _month(request)
    data = loss_report.report(month, _month_end(month))
    if request.GET.get("export") == "xlsx":
        return _losses_xlsx(data, request.user.has_perm(COSTS))
    return render(
        request,
        "production/losses.html",
        {
            "month": month,
            "previous_month": (month - datetime.timedelta(days=1)).replace(day=1),
            "next_month": (month + datetime.timedelta(days=32)).replace(day=1),
            "report": data,
            "show_money": request.user.has_perm(COSTS),
        },
    )


def _losses_xlsx(data, show_money):
    headers = [
        _("Material"), _("Unit"), _("Use by norm"), _("Allowed, %"), _("Production defect"), _("Write-off"),
        _("Stock-count shortage"), _("Losses"), _("Losses to the norm, %"), _("Over the allowed"),
    ]  # fmt: skip
    if show_money:
        headers += [_("Price"), _("Losses, UZS"), _("Over the allowed, UZS")]
    lines = []
    for row in data.materials:
        values = [
            row.material.name, row.material.unit, row.norm, row.allowed_percent, row.defect, row.write_off,
            row.shortage, row.losses, row.percent, row.over_allowed,
        ]  # fmt: skip
        if show_money:
            values += [row.price.value, costing.money(row.losses_value), costing.money(row.over_allowed_value)]
        lines.append(values)
    sheets = [exports.Sheet(_("Losses"), headers, lines, money_columns={10, 11, 12} if show_money else ())]
    reason_headers = [_("Defect reason"), _("Entries")] + ([_("Losses, UZS")] if show_money else [])
    reason_lines = [
        [group.label or _("No reason given"), group.entries] + ([costing.money(group.value)] if show_money else [])
        for group in data.reasons
    ]
    money = {2} if show_money else ()
    sheets.append(exports.Sheet(_("Defect reasons"), reason_headers, reason_lines, money_columns=money))
    return exports.xlsx_response(f"losses-{data.start:%Y-%m}.xlsx", sheets)


SHOW = ("attention", "expired", "all")


@permission(view="production.view_product")
def expiry_page(request):
    """Finished-goods batches and material deliveries in stock, with the date each is good until."""
    show = request.GET.get("show") if request.GET.get("show") in SHOW else "attention"
    today = timezone.localdate()
    groups = []
    for key, title, allowed, lots in (
        ("goods", _("Finished goods"), True, None),
        ("materials", _("Raw materials"), request.user.has_perm("production.view_material"), None),
    ):
        if not allowed:
            continue
        lots = expiry.product_lots(today) if key == "goods" else expiry.material_lots(today)
        summary = expiry.summary(lots)
        if show == "expired":
            lots = [lot for lot in lots if lot.status == expiry.Status.EXPIRED]
        elif show == "attention":
            lots = [lot for lot in lots if lot.status in (expiry.Status.EXPIRED, expiry.Status.SOON)]
        groups.append({"key": key, "title": title, "lots": lots, "summary": summary})
    return render(
        request,
        "production/expiry.html",
        {"groups": groups, "show": show, "soon_days": expiry.SOON_DAYS, "today": today},
    )


@require_POST
@permission(view="production.view_product", change="production.change_materialmove")
def expiry_dates(request):
    """Save when opening-balance stock really arrived (received_<movement id>): shelf life counts from it."""
    today = timezone.localdate()
    changed, refused = 0, 0
    for name, value in request.POST.items():
        if not name.startswith("received_") or not name[9:].isdigit():
            continue
        move = MaterialMove.objects.filter(pk=name[9:], quantity__gt=0, run__isnull=True).first()
        if move is None or move.source.startswith("receipt:"):
            continue  # a receipt's date is the receipt's own
        date = parse_date(value) if value else None
        if value and (date is None or date > today):
            refused += 1
            continue
        if date == move.date:
            date = None  # the same day as the ledger's: nothing to keep
        if move.received_on != date:
            move.received_on = date
            move.save(update_fields=["received_on"])
            changed += 1
    if refused:
        messages.error(request, _("A day in the future can't be when it arrived: those were left as they were."))
    if changed:
        messages.success(request, _("Saved: shelf life now counts from the days it really arrived."))
    elif not refused:
        messages.info(request, _("Nothing was changed."))
    show = request.POST.get("show") if request.POST.get("show") in SHOW else "all"
    return redirect(f"{reverse('production:expiry')}?show={show}#materials")
