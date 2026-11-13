"""The Production dashboard (Производство › Обзор): output, plan, materials, goods in transit, finished goods.

Every block is worked out from the same records as its own page, for a period (today,
this week, this month… or any dates), and shown only to users who may open that page.
"Days of cover" is how long the stock lasts at the average daily use (materials) or
shipping (finished goods) of the 30 days to the period's end.
"""

import datetime
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Case, F, Sum, Value, When
from django.utils import timezone

from finance_saber.core.dashboard import Period, products_month

from . import expiry
from .models import (
    Material,
    MaterialMove,
    MaterialOrder,
    MaterialReceipt,
    Product,
    ProductionRun,
    ProductShipmentLine,
    material_stock,
    materials_in_transit,
    product_stock,
)

ZERO = Decimal(0)
USAGE_DAYS = 30  # the window for the average daily use and shipping
COVER_WARNING = 14  # days: fewer is "runs out soon"
LIST_SIZE = 8


def days(period: Period) -> list[datetime.date]:
    return [period.date_from + datetime.timedelta(days=n) for n in range(period.days)]


def output(period: Period) -> dict:
    """Posted production in the period: units, litres, working days; and drafts still to post."""
    runs = ProductionRun.objects.posted().filter(date__gte=period.date_from, date__lte=period.date_to)
    units = runs.aggregate(total=Sum("quantity"))["total"] or 0
    litres = runs.aggregate(total=Sum(F("quantity") * F("product__volume")))["total"] or ZERO
    working_days = runs.values("date").distinct().count()
    by_product = list(runs.values("product__name").annotate(units=Sum("quantity")).order_by("-units")[:LIST_SIZE])
    drafts = ProductionRun.objects.filter(status=ProductionRun.Status.DRAFT).select_related("product")
    return {
        "units": units,
        "litres": Decimal(litres).quantize(Decimal("1")),
        "working_days": working_days,
        "per_day": round(units / working_days) if working_days else 0,
        "by_product": by_product,
        "drafts": list(drafts.order_by("-date")[:LIST_SIZE]),
        "draft_count": drafts.count(),
    }


def shipped(period: Period) -> dict:
    """Units shipped in the period, less customer returns (no money: that is for Settlements)."""
    lines = ProductShipmentLine.objects.filter(
        shipment__is_cancelled=False, shipment__date__gte=period.date_from, shipment__date__lte=period.date_to
    )
    sign = Case(When(shipment__is_return=True, then=Value(-1)), default=Value(1))
    units = lines.aggregate(total=Sum(F("quantity") * sign))["total"] or 0
    customers = lines.filter(shipment__is_return=False).values("shipment__customer").distinct().count()
    return {"units": units, "customers": customers}


def chart(period: Period, with_shipments: bool) -> dict:
    """Units produced (and shipped) per day, or per month for a period over two months."""
    monthly = period.days > 62
    produced = defaultdict(int)
    for row in (
        ProductionRun.objects.posted()
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .values("date")
        .annotate(units=Sum("quantity"))
    ):
        produced[row["date"].replace(day=1) if monthly else row["date"]] += row["units"]
    out = defaultdict(int)
    if with_shipments:
        sign = Case(When(shipment__is_return=True, then=Value(-1)), default=Value(1))
        for row in (
            ProductShipmentLine.objects.filter(
                shipment__is_cancelled=False, shipment__date__gte=period.date_from, shipment__date__lte=period.date_to
            )
            .values("shipment__date")
            .annotate(units=Sum(F("quantity") * sign))
        ):
            date = row["shipment__date"]
            out[date.replace(day=1) if monthly else date] += row["units"]
    if monthly:
        keys = sorted({day.replace(day=1) for day in days(period)})
        labels = [key.strftime("%m.%Y") for key in keys]
    else:
        keys = days(period)
        labels = [key.strftime("%d.%m") for key in keys]
    data = {"labels": labels, "produced": [produced.get(key, 0) for key in keys], "monthly": monthly}
    if with_shipments:
        data["shipped"] = [out.get(key, 0) for key in keys]
    return data


def _daily_average(moves, field: str, end: datetime.date) -> dict[int, Decimal]:
    start = end - datetime.timedelta(days=USAGE_DAYS - 1)
    rows = moves.filter(date__gte=start, date__lte=end).values(field).annotate(total=Sum("quantity"))
    return {row[field]: -row["total"] / USAGE_DAYS for row in rows if row["total"] and row["total"] < 0}


def cover(stock: Decimal, per_day: Decimal | None) -> int | None:
    """Days the stock lasts at ``per_day``; None when nothing is used."""
    if not per_day:
        return None
    return max(int(stock / per_day), 0)


def materials(period: Period, with_transit: bool) -> dict:
    """Materials at risk: below zero, under their minimum, or lasting under two weeks at the recent use."""
    stock = material_stock()
    use = _daily_average(
        MaterialMove.objects.posted().filter(kind=MaterialMove.Kind.CONSUMPTION), "material_id", period.date_to
    )
    transit = materials_in_transit() if with_transit else {}
    rows = []
    for material in Material.objects.filter(is_active=True):
        amount = stock.get(material.pk, ZERO)
        days_left = cover(amount, use.get(material.pk))
        below_min = bool(material.min_stock) and amount < material.min_stock
        if amount < 0:
            level = "negative"
        elif below_min or (days_left is not None and days_left < COVER_WARNING):
            level = "low"
        else:
            continue
        rows.append({
            "material": material, "stock": amount, "per_day": use.get(material.pk), "days": days_left,
            "below_min": below_min, "level": level, "transit": transit.get(material.pk),
        })  # fmt: skip
    rows.sort(key=lambda row: (row["level"] != "negative", row["days"] if row["days"] is not None else 9999))
    return {"rows": rows[:LIST_SIZE], "count": len(rows), "negative": sum(row["level"] == "negative" for row in rows)}


def transit() -> dict:
    """Supplier orders on the way now: how many, how many late, and the next arrivals."""
    open_orders = MaterialOrder.objects.filter(status__in=MaterialOrder.OPEN).select_related("supplier")
    late = open_orders.filter(expected_date__lt=timezone.localdate())
    return {
        "count": open_orders.count(),
        "late": late.count(),
        "next": list(open_orders.prefetch_related("lines__material").order_by("expected_date")[:LIST_SIZE]),
    }


# The order of the finished goods list: what needs action first, then the rest of the stock.
GOODS_ORDER = ("expired", "soon", "negative", "good", "unknown", "empty")


def _goods_state(amount: Decimal, lots: list) -> str:
    """expired / soon / good: its first batch to go off; unknown: in stock without a shelf life;
    negative: shipped more than production entered; empty: nothing in stock."""
    if amount < 0:
        return "negative"
    if amount == 0:
        return "empty"
    if lots and lots[0].best_before:
        return lots[0].status
    return "unknown"


def goods(period: Period, lots_by_product: dict | None = None) -> dict:
    """Finished goods: stock, how many days each lasts at the recent shipping, and its batches in
    stock with their own good-until dates (``lots_by_product`` from expiry.by_item)."""
    lots_by_product = lots_by_product or {}
    stock = product_stock()
    sign = Case(When(shipment__is_return=True, then=Value(-1)), default=Value(1))
    start = period.date_to - datetime.timedelta(days=USAGE_DAYS - 1)
    shipped_recently = {
        row["product_id"]: row["units"]
        for row in ProductShipmentLine.objects.filter(
            shipment__is_cancelled=False, shipment__affects_stock=True,
            shipment__date__gte=start, shipment__date__lte=period.date_to,
        )
        .values("product_id")
        .annotate(units=Sum(F("quantity") * sign))
    }  # fmt: skip
    rows = []
    for product in Product.objects.filter(is_active=True):
        amount = stock.get(product.pk, ZERO)
        per_day = Decimal(shipped_recently.get(product.pk, 0)) / USAGE_DAYS
        # Batches only when there is stock: below zero, what batches show left isn't real.
        lots = lots_by_product.get(product.pk, []) if amount > 0 else []
        rows.append({
            "product": product, "stock": amount, "days": cover(amount, per_day), "lots": lots,
            "first": lots[0] if lots else None, "state": _goods_state(amount, lots),
        })  # fmt: skip
    rows.sort(
        key=lambda row: (
            GOODS_ORDER.index(row["state"]),
            row["first"].best_before if row["first"] and row["first"].best_before else datetime.date.max,
            row["days"] if row["days"] is not None else 9999,
            row["product"].name,
        )
    )
    states = Counter(row["state"] for row in rows)
    return {
        "rows": rows,
        "units": sum((row["stock"] for row in rows if row["stock"] > 0), ZERO),
        "negative": states["negative"],
        "batches": sum(len(row["lots"]) for row in rows),
        "expired": sum(lot.status == "expired" for row in rows for lot in row["lots"]),
        "soon": sum(lot.status == "soon" for row in rows for lot in row["lots"]),
    }


def receipts() -> list:
    return list(
        MaterialReceipt.objects.posted()
        .select_related("supplier")
        .prefetch_related("lines")
        .order_by("-date", "-id")[:5]
    )


def build(user, period: Period) -> dict:
    """The blocks this user may see, for the period."""
    can = user.has_perm
    context = {}
    if can("production.view_productionrun"):
        context["output"] = output(period)
        context["chart"] = chart(period, with_shipments=can("production.view_productshipment"))
    if can("production.view_productshipment"):
        context["shipped"] = shipped(period)
    if can("sales.view_plan") and can("production.view_productionrun"):
        plan = products_month(period, show_sales=False, show_production=True)
        # The biggest plans first: the dashboard shows the top of the list, the plan page the rest.
        plan["rows"].sort(key=lambda row: -row["production"]["plan"])
        context["plan"] = plan
        if total := plan["totals"].get("production"):
            context["plan_percent"] = total["percent"]
    if can("production.view_material"):
        context["materials"] = materials(period, with_transit=can("production.view_materialorder"))
    if can("production.view_materialorder"):
        context["transit"] = transit()
    if can("production.view_product"):
        lots = expiry.product_lots()
        context["goods"] = goods(period, expiry.by_item(lots))
        context["expiring"] = expiry.summary(lots)
        context["shelf_alerts"] = expiry.alerts(lots)  # batches to check: 3 months or less left
    if can("production.view_materialreceipt"):
        context["receipts"] = receipts()
    return context
