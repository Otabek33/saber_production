"""Shelf life: what is in stock, and until when it is good.

Shelf life is typed as text, the way people write it ("18 месяц", "24 мес", "2 года", "5 г",
"180 дней", "6 нед", "12 months"); ``parse`` reads it, ``best_before`` adds it to a date.

* Finished goods: each batch still in stock (first in, first out: traceability.trace_products)
  is good until its production date + the product's shelf life.
* Raw materials: each delivery's own "good until" (typed from its label when it arrived); else
  the material's date (typed on the material); else an older text shelf life (from the
  materials Excel), counted from the delivery's receipt date. Stock entered as an opening
  balance really arrived earlier: its "actually received" day (MaterialMove.received_on, set on
  the shelf-life page) is counted from instead, without touching the stock ledger.

Stock that has no date of its own (an opening balance, a count surplus, a customer's return)
is listed with the date it came in, so nothing in stock is left out.
"""

import calendar
import datetime
import re
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from . import traceability
from .models import Material, Product

SOON_DAYS = 30
ZERO = Decimal(0)

# The unit's first letters, in Russian or English; months unless it says otherwise.
UNITS = (
    (("д", "day", "сут"), "days"),
    (("н", "w"), "weeks"),
    (("г", "л", "y"), "years"),
    (("м", "mo"), "months"),
)
NUMBER_AND_UNIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([^\d\s.,]*)")


def parse(text: str) -> tuple[int, str] | None:
    """("18 месяц") -> (18, "months"); ("5 г") -> (5, "years"); None when it can't be read."""
    match = NUMBER_AND_UNIT.search((text or "").strip().lower())
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    word = match.group(2)
    if not word or number <= 0:
        return None
    for prefixes, unit in UNITS:
        if word.startswith(prefixes):
            if number != int(number):  # "1,5 года" -> 18 months
                return (round(number * 12), "months") if unit == "years" else (round(number), unit)
            return int(number), unit
    return None


def add_months(date: datetime.date, months: int) -> datetime.date:
    month = date.month - 1 + months
    year, month = date.year + month // 12, month % 12 + 1
    return date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1]))


def best_before(date: datetime.date, shelf_life: str) -> datetime.date | None:
    """The last good day of something made (or received) on ``date``; None without a readable shelf life."""
    parsed = parse(shelf_life)
    if parsed is None or date is None:
        return None
    number, unit = parsed
    if unit == "days":
        return date + datetime.timedelta(days=number)
    if unit == "weeks":
        return date + datetime.timedelta(weeks=number)
    return add_months(date, number * 12 if unit == "years" else number)


class Status:
    EXPIRED = "expired"
    SOON = "soon"
    GOOD = "good"
    UNKNOWN = "unknown"  # no readable shelf life


@dataclass
class StockLot:
    """Something in stock with its date: a batch of a product, or a delivery of a material."""

    item: Product | Material
    lot: traceability.Lot
    best_before: datetime.date | None
    today: datetime.date

    @property
    def remaining(self) -> Decimal:
        return self.lot.remaining

    @property
    def date(self) -> datetime.date:
        """When it came in: the day it really arrived, when that was given (an opening balance), else its date."""
        return getattr(self.lot.move, "received_on", None) or self.lot.date

    @property
    def editable(self) -> bool:
        """Its arrival day can be corrected: stock that didn't come by a receipt or a batch (an opening balance)."""
        return self.lot.receipt is None and self.lot.run is None and hasattr(self.lot.move, "received_on")

    @property
    def run(self):
        return self.lot.run

    @property
    def receipt(self):
        return self.lot.receipt

    @property
    def code(self) -> str:
        return traceability.batch_code(self.lot.run) if self.lot.run is not None else ""

    @property
    def days_left(self) -> int | None:
        return (self.best_before - self.today).days if self.best_before else None

    @property
    def status(self) -> str:
        if self.best_before is None:
            return Status.UNKNOWN
        if self.days_left < 0:
            return Status.EXPIRED
        return Status.SOON if self.days_left <= SOON_DAYS else Status.GOOD


def _in_stock(traced, items, today, lot_dates=None) -> list[StockLot]:
    lots = []
    for lot in traced.lots:
        if lot.remaining <= 0:
            continue
        item = items[getattr(lot.move, "product_id", None) or lot.move.material_id]
        arrived = getattr(lot.move, "received_on", None) or lot.date
        date = (
            (lot_dates or {}).get(lot.receipt_line_id)
            or getattr(item, "best_before", None)
            or best_before(arrived, item.shelf_life)
        )
        lots.append(StockLot(item=item, lot=lot, best_before=date, today=today))
    # The first to go off first; those without a shelf life last.
    lots.sort(key=lambda stock: (stock.best_before is None, stock.best_before or stock.date, stock.item.name))
    return lots


def product_lots(today: datetime.date | None = None) -> list[StockLot]:
    """Every batch of finished goods still in stock, the first to expire first."""
    products = Product.objects.in_bulk()
    return _in_stock(traceability.trace_products(list(products)), products, today or timezone.localdate())


def material_lots(today: datetime.date | None = None) -> list[StockLot]:
    """Every delivery of raw material still in stock, the first to expire first."""
    from .models import MaterialReceiptLine

    materials = Material.objects.in_bulk()
    lot_dates = dict(MaterialReceiptLine.objects.filter(best_before__isnull=False).values_list("pk", "best_before"))
    return _in_stock(traceability.trace(list(materials)), materials, today or timezone.localdate(), lot_dates)


def summary(lots: list[StockLot]) -> dict:
    """How many lots (and units) are expired or expire soon, and how many items have no shelf life."""
    expired = [lot for lot in lots if lot.status == Status.EXPIRED]
    soon = [lot for lot in lots if lot.status == Status.SOON]
    return {
        "expired": len(expired),
        "expired_units": sum((lot.remaining for lot in expired), ZERO),
        "soon": len(soon),
        "soon_units": sum((lot.remaining for lot in soon), ZERO),
        "unknown_items": len({lot.item.pk for lot in lots if lot.status == Status.UNKNOWN}),
    }
