"""Supplier record: do they deliver on time, and what do their materials cost over time.

* Deliveries: each receipt line that came for a supplier order line, against that line's
  expected arrival: on time (on or before it) or late by so many days. Lines still to come
  and already past their date count as late now.
* Prices: the price per unit of each priced receipt line (amount / quantity, UZS with VAT),
  by material, oldest first, with the change from the previous price of the same material.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from finance_saber.cashbook.models import Company

from .models import MaterialOrder, MaterialOrderLine, MaterialReceiptLine, with_received

ZERO = Decimal(0)


@dataclass
class Delivery:
    line: MaterialReceiptLine
    expected: datetime.date | None  # None: not for an order

    @property
    def date(self) -> datetime.date:
        return self.line.receipt.date

    @property
    def delay(self) -> int | None:
        return (self.date - self.expected).days if self.expected else None

    @property
    def on_time(self) -> bool | None:
        return None if self.delay is None else self.delay <= 0


@dataclass
class SupplierRecord:
    supplier: Company
    deliveries: list[Delivery] = field(default_factory=list)
    late_now: list[MaterialOrderLine] = field(default_factory=list)
    amount: Decimal = ZERO  # bought in the period, UZS

    @property
    def receipts(self) -> int:
        return len({delivery.line.receipt_id for delivery in self.deliveries})

    @property
    def for_orders(self) -> list[Delivery]:
        return [delivery for delivery in self.deliveries if delivery.expected]

    @property
    def on_time_percent(self) -> int | None:
        counted = self.for_orders
        if not counted:
            return None
        return round(100 * sum(1 for delivery in counted if delivery.on_time) / len(counted))

    @property
    def average_delay(self) -> Decimal | None:
        late = [delivery.delay for delivery in self.for_orders if delivery.delay > 0]
        return (Decimal(sum(late)) / len(late)).quantize(Decimal("0.1")) if late else None

    @property
    def last_delivery(self) -> datetime.date | None:
        return max((delivery.date for delivery in self.deliveries), default=None)


def _receipt_lines():
    return MaterialReceiptLine.objects.filter(receipt__is_cancelled=False).select_related(
        "receipt", "material", "order_line__order"
    )


def records(start: datetime.date, end: datetime.date) -> list[SupplierRecord]:
    """Every supplier that delivered in the period or has an order late now; the least punctual first."""
    found: dict[int, SupplierRecord] = {}
    lines = (
        _receipt_lines().filter(receipt__date__gte=start, receipt__date__lte=end).select_related("receipt__supplier")
    )
    for line in lines:
        record = found.setdefault(line.receipt.supplier_id, SupplierRecord(supplier=line.receipt.supplier))
        record.deliveries.append(Delivery(line, line.order_line.expected if line.order_line_id else None))
        record.amount += line.amount
    for line in late_lines():
        supplier = line.order.supplier
        found.setdefault(supplier.pk, SupplierRecord(supplier=supplier)).late_now.append(line)
    return sorted(
        found.values(),
        key=lambda record: (
            -len(record.late_now),
            record.on_time_percent if record.on_time_percent is not None else 101,
            record.supplier.name,
        ),
    )


def late_lines(supplier: Company | None = None) -> list[MaterialOrderLine]:
    """Order lines still to come whose expected arrival has passed, the latest first."""
    today = timezone.localdate()
    lines = with_received(
        MaterialOrderLine.objects.filter(order__status__in=MaterialOrder.OPEN)
        .filter(Q(expected_date__lt=today) | Q(expected_date__isnull=True, order__expected_date__lt=today))
        .select_related("order__supplier", "material")
    )
    if supplier is not None:
        lines = lines.filter(order__supplier=supplier)
    return sorted((line for line in lines if line.remaining), key=lambda line: line.expected)


@dataclass
class PricePoint:
    line: MaterialReceiptLine
    price: Decimal
    previous: Decimal | None = None

    @property
    def change(self) -> Decimal | None:
        if not self.previous:
            return None
        return ((self.price - self.previous) / self.previous * 100).quantize(Decimal("0.1"))


def price_history(supplier: Company) -> list[dict]:
    """{material, points (oldest first), last, first, change since the first} per material bought from them."""
    points: dict[int, list[PricePoint]] = defaultdict(list)
    lines = _receipt_lines().filter(receipt__supplier=supplier, amount__gt=0).order_by("receipt__date", "pk")
    for line in lines:
        history = points[line.material_id]
        price = (line.amount / line.quantity).quantize(Decimal("0.01"))
        history.append(PricePoint(line, price, history[-1].price if history else None))
    result = []
    for history in points.values():
        first, last = history[0].price, history[-1].price
        change = ((last - first) / first * 100).quantize(Decimal("0.1")) if first and len(history) > 1 else None
        result.append({"material": history[0].line.material, "points": history, "last": last, "change": change})
    return sorted(result, key=lambda item: item["material"].name)


def deliveries(supplier: Company, start: datetime.date, end: datetime.date) -> SupplierRecord:
    record = SupplierRecord(supplier=supplier, late_now=late_lines(supplier))
    lines = _receipt_lines().filter(receipt__supplier=supplier, receipt__date__gte=start, receipt__date__lte=end)
    for line in lines.order_by("-receipt__date", "-pk"):
        record.deliveries.append(Delivery(line, line.order_line.expected if line.order_line_id else None))
        record.amount += line.amount
    return record
