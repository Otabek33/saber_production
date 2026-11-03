"""The yearly sales and production plan: reading the plan workbook, and the plan next to the fact.

The sales fact is what left the warehouse: Production › Shipments, less customer
returns (the old daily sales were moved there; their tables are kept, unused).
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Case, F, Q, Sum, Value, When
from django.utils.translation import gettext as _
from openpyxl import load_workbook

from finance_saber.production.importer import clean, key
from finance_saber.production.models import Product, ProductAlias, ProductionRun, ProductShipmentLine, product_lookup

from .models import Plan

# Product names in SmartUp and the Plan sheet that differ from the products' own names.
PRODUCT_ALIASES = {
    "7 Energy 450 ML 1/24": "7 Energy Drink 0,450 л",
    "ЖБ 450 Мл Sakura Зеленый чай с мёдом": "Green Tea мёд 0,450 л",
    "ЖБ 450 Мл Sakura Черный чай апельсин-корица": "Blak Tea Апельсин-корица 0,450 л",
    "ЖБ 450 Мл Sakura Черный чай лимон-имбирь-мёд": "Blak Tea лимон-мёд-имбир 0,450 л",
    "ЖБ 450 Мл Sakura Черный чай яблоко-корица-гвоздика": "Blak Tea Яблока - корица-гвоздика 0,450 л",
}


class SheetError(Exception):
    pass


def seed_product_aliases() -> int:
    """Create the known SmartUp / Plan aliases for products that exist. Returns how many were added."""
    products = {key(name): name for name in PRODUCT_ALIASES.values()}
    by_key = {key(p.name): p for p in Product.objects.all() if key(p.name) in products}
    added = 0
    for alias, product_name in PRODUCT_ALIASES.items():
        product = by_key.get(key(product_name))
        if product:
            added += ProductAlias.objects.get_or_create(name=alias, defaults={"product": product, "source": "SmartUp"})[
                1
            ]
    return added


def shipped_units(start, end, by_month=False) -> dict:
    """Units shipped from ``start`` to ``end`` less customer returns: {product id: units}, or with by_month
    {(product id, month): units}."""
    lines = ProductShipmentLine.objects.filter(
        shipment__is_cancelled=False, shipment__date__gte=start, shipment__date__lte=end
    )
    sign = Case(When(shipment__is_return=True, then=Value(-1)), default=Value(1))
    fields = ["product_id", "shipment__date__month"] if by_month else ["product_id"]
    rows = lines.values(*fields).annotate(total=Sum(F("quantity") * sign))
    if by_month:
        return {(row["product_id"], row["shipment__date__month"]): row["total"] for row in rows}
    return {row["product_id"]: row["total"] for row in rows}


# Reading workbooks ----------------------------------------------------------------------


def _open_workbook(file):
    """An .xlsx path or upload, read-only with formula values; the caller closes it."""
    try:
        return load_workbook(file, data_only=True, read_only=True)
    except Exception as error:  # zip / xml errors: not a readable .xlsx
        label = getattr(file, "name", str(file))
        raise SheetError(_("%(file)s is not a readable .xlsx workbook.") % {"file": label}) from error


def _to_date(value) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _to_decimal(value) -> Decimal | None:
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.replace("\xa0", "").replace(" ", "").replace(",", "."))
        except InvalidOperation:
            return None
    return None


# The yearly plan ("Plan 2026") -----------------------------------------------------------

PLAN_TITLES = {"планреализации": Plan.Kind.SALES, "планпроизводства": Plan.Kind.PRODUCTION}


@dataclass
class PlanFile:
    year: int | None
    # {kind: {product name: {month: quantity}}}
    blocks: dict = field(default_factory=dict)


def read_plan(file, sheet_name=None) -> PlanFile:
    """The sales and production blocks of a yearly plan sheet: a title row ("Всего план реализации
    на 2026 год"), a header with the months as dates from column D, then one row per product
    (name in C)."""
    workbook = _open_workbook(file)
    try:
        names = [sheet_name] if sheet_name else [n for n in workbook.sheetnames if key(n).startswith("plan")]
        names += [n for n in workbook.sheetnames if n not in names]
        for name in names:
            parsed = _parse_plan(list(workbook[name].iter_rows(values_only=True)))
            if parsed.blocks:
                return parsed
    finally:
        workbook.close()
    raise SheetError(_("No plan sheet found: expected a block titled “Всего план реализации на … год”."))


def _parse_plan(rows) -> PlanFile:
    result = PlanFile(year=None)
    index = 0
    while index < len(rows):
        title = key(" ".join(str(value) for value in rows[index] if isinstance(value, str)))
        kind = next((kind for prefix, kind in PLAN_TITLES.items() if key(prefix) in title), None)
        if kind is None or kind in result.blocks:
            index += 1
            continue
        header = rows[index + 1] if index + 1 < len(rows) else ()
        months = {}
        for column, value in enumerate(header):
            date = _to_date(value)
            if date:
                months[column] = date.month
                result.year = result.year or date.year
        block = {}
        row = index + 3  # title, header, "План" row
        while row < len(rows):
            values = rows[row]
            name = clean(values[2] if len(values) > 2 else "")
            if not name:
                break
            block[name] = {
                month: int(_to_decimal(values[column]) or 0)
                for column, month in months.items()
                if column < len(values) and _to_decimal(values[column]) is not None
            }
            row += 1
        result.blocks[kind] = block
        index = row
    return result


@dataclass
class PlanImportReport:
    year: int | None = None
    products: dict = field(default_factory=dict)  # {kind: number of products}
    values: int = 0
    unknown_products: list[str] = field(default_factory=list)
    saved: bool = False


@transaction.atomic
def import_plan(parsed: PlanFile, dry_run=False) -> PlanImportReport:
    """Write the plan's figures (replacing that year's earlier ones). Nothing is saved if a product is unknown."""
    report = PlanImportReport(year=parsed.year)
    seed_product_aliases()
    products = product_lookup()
    unknown = {name for block in parsed.blocks.values() for name in block if key(name) not in products}
    report.unknown_products = sorted(unknown)
    for kind, block in parsed.blocks.items():
        report.products[kind] = len(block)
        report.values += sum(len(months) for months in block.values())
    if unknown or dry_run or not parsed.year:
        return report
    for kind, block in parsed.blocks.items():
        for name, months in block.items():
            for month, quantity in months.items():
                Plan.objects.update_or_create(
                    kind=kind,
                    year=parsed.year,
                    month=month,
                    product=products[key(name)],
                    defaults={"quantity": max(quantity, 0)},
                )
    report.saved = True
    return report


def plan_vs_fact(kind: str, year: int):
    """Rows of {product, months: [(plan, fact)…12], plan, fact} and the month totals."""
    plans = defaultdict(dict)
    for plan in Plan.objects.filter(kind=kind, year=year):
        plans[plan.product_id][plan.month] = plan.quantity
    facts = defaultdict(dict)
    if kind == Plan.Kind.SALES:
        shipped = shipped_units(datetime.date(year, 1, 1), datetime.date(year, 12, 31), by_month=True)
        for (product_id, month), units in shipped.items():
            facts[product_id][month] = units
    else:
        fact_rows = (
            ProductionRun.objects.posted()
            .filter(date__year=year)
            .values("product_id", "date__month")
            .annotate(total=Sum("quantity"))
        )
        for row in fact_rows:
            facts[row["product_id"]][row["date__month"]] = row["total"]
    rows = []
    for product in Product.objects.filter(Q(is_active=True) | Q(pk__in=plans) | Q(pk__in=facts)):
        months = [(plans[product.pk].get(m, 0), facts[product.pk].get(m, 0)) for m in range(1, 13)]
        rows.append(
            {
                "product": product,
                "months": months,
                "plan": sum(p for p, _f in months),
                "fact": sum(f for _p, f in months),
            }
        )
    totals = [(sum(row["months"][m][0] for row in rows), sum(row["months"][m][1] for row in rows)) for m in range(12)]
    return rows, totals
