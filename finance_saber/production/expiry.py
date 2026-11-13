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

Alerts (``alert_level``): a batch is flagged 3, 2 and 1 months before it goes off, then 2 weeks
before, then as expired; the warehouse checks it. They show in the header's notifications (each
morning from 06:00, production/notifications.py), on the production overview and the shelf-life page.
"""

import calendar
import datetime
import re
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy

from . import traceability
from .models import Material, Product

SOON_DAYS = 30
ZERO = Decimal(0)
ALERT_WEEKS_DAYS = 14  # the last alert before a batch goes off: 2 weeks
# Alert levels, the most urgent first: key, how it reads, Bootstrap colour.
ALERT_LEVELS = (
    ("expired", gettext_lazy("Expired"), "danger"),
    ("weeks_2", gettext_lazy("2 weeks left"), "danger"),
    ("month_1", gettext_lazy("1 month left"), "warning"),
    ("months_2", gettext_lazy("2 months left"), "warning"),
    ("months_3", gettext_lazy("3 months left"), "info"),
)
ALERT_LABELS = {key: label for key, label, _tone in ALERT_LEVELS}
ALERT_TONES = {key: tone for key, _label, tone in ALERT_LEVELS}

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


def months_text(months: int) -> str:
    """A number of months as it is stored and read back by ``parse``: 1 месяц, 2 месяца, 12 месяцев."""
    if months % 10 == 1 and months % 100 != 11:
        word = "месяц"
    elif months % 10 in (2, 3, 4) and months % 100 not in (12, 13, 14):
        word = "месяца"
    else:
        word = "месяцев"
    return f"{months} {word}"


def in_months(shelf_life: str) -> int | None:
    """A shelf life as a whole number of months (days and weeks rounded); None when it can't be read."""
    parsed = parse(shelf_life)
    if parsed is None:
        return None
    number, unit = parsed
    days = {"days": number, "weeks": number * 7, "months": number * 30, "years": number * 365}[unit]
    return max(1, round(days / 30))


def add_months(date: datetime.date, months: int) -> datetime.date:
    month = date.month - 1 + months
    year, month = date.year + month // 12, month % 12 + 1
    return date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1]))


def alert_level(best_before: datetime.date | None, today: datetime.date) -> str | None:
    """Which alert a batch good until ``best_before`` is at (ALERT_LEVELS), or None: more than
    3 months left, or no shelf life. Months are calendar months from today."""
    if best_before is None:
        return None
    if best_before < today:
        return "expired"
    if best_before <= today + datetime.timedelta(days=ALERT_WEEKS_DAYS):
        return "weeks_2"
    for months, key in ((1, "month_1"), (2, "months_2"), (3, "months_3")):
        if best_before <= add_months(today, months):
            return key
    return None


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
    def alert(self) -> str | None:
        """Its alert level (ALERT_LEVELS) or None."""
        return alert_level(self.best_before, self.today)

    @property
    def alert_label(self) -> str:
        return ALERT_LABELS.get(self.alert, "")

    @property
    def alert_tone(self) -> str:
        return ALERT_TONES.get(self.alert, "secondary")

    @property
    def tone(self) -> str:
        """The Bootstrap colour of its status: danger expired, warning soon, success good."""
        return {Status.EXPIRED: "danger", Status.SOON: "warning", Status.GOOD: "success"}.get(self.status, "secondary")

    @property
    def life_left_percent(self) -> int | None:
        """How much of its shelf life is still ahead, 0–100 (for a progress bar); None without one."""
        if self.best_before is None:
            return None
        total = (self.best_before - self.date).days
        if total <= 0:
            return 0
        return max(0, min(100, round(100 * (self.best_before - self.today).days / total)))

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


def by_item(lots: list[StockLot]) -> dict[int, list[StockLot]]:
    """Each item's lots in stock, the first to go off first (``lots`` come sorted that way)."""
    grouped = {}
    for lot in lots:
        grouped.setdefault(lot.item.pk, []).append(lot)
    return grouped


def alerts(lots: list[StockLot]) -> dict:
    """The lots to check at the warehouse (3 months or less left, or expired), the first to go off
    first, and how many are at each level."""
    flagged = [lot for lot in lots if lot.alert]
    levels = [
        {"key": key, "label": label, "tone": tone, "count": sum(lot.alert == key for lot in flagged)}
        for key, label, tone in ALERT_LEVELS
    ]
    urgent = sum(lot.alert in ("expired", "weeks_2") for lot in flagged)
    return {"lots": flagged, "levels": levels, "count": len(flagged), "urgent": urgent}


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
