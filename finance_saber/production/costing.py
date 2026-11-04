"""Production cost: what each batch's materials cost, per unit, and the margin on shipments.

Worked out from the records whenever it is shown, never stored, the same way as batch
traceability (traceability.py), which it builds on:

* A batch's materials are matched to the deliveries they came from, first in, first out,
  and each part is valued at that delivery's price (receipt line amount / quantity, in UZS
  with VAT: a USD order's receipt is already converted at its rate).
* Where that price isn't known, an estimate stands in, in this order: the latest priced
  receipt of the material up to that day (else the first one after it), then the
  material's standard price. Stock that didn't come from a receipt (the opening balance, a
  count surplus) and batches imported as history (no stock movements) are valued this way.
  An estimated part is marked, so a page can say how much of a cost is estimated.
* A shipment's cost is what its units cost to make: the batches it was shipped from (first
  in, first out), at each batch's cost per unit. Shipments loaded as history (no stock
  movements) and units from other stock take the product's cost per unit at the date: the
  latest batch up to that day, else the first one after.

Margin = shipment amount − cost. Both are with VAT, as entered. A return counts negative.
"""

import bisect
import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from . import traceability
from .models import (
    Material,
    MaterialMove,
    MaterialReceiptLine,
    ProductionRun,
    ProductMove,
    ProductShipment,
    ProductShipmentLine,
    RunLine,
)

ZERO = Decimal(0)
CENT = Decimal("0.01")


class Source:
    """Where a price came from."""

    RECEIPT = "receipt"  # the delivery actually used
    ESTIMATE = "estimate"  # another receipt of the material, near that day
    STANDARD = "standard"  # the material's standard price
    NONE = "none"  # nothing known

    EXACT = {RECEIPT}


@dataclass(frozen=True)
class Price:
    value: Decimal | None  # UZS per unit; None: unknown
    source: str

    @property
    def known(self) -> bool:
        return self.value is not None


UNKNOWN = Price(None, Source.NONE)


@dataclass
class Part:
    """Some quantity valued at one price."""

    quantity: Decimal
    price: Price

    @property
    def amount(self) -> Decimal | None:
        return self.quantity * self.price.value if self.price.known else None


@dataclass
class Valued:
    """Parts valued together: a total, and how much of it is estimated or unknown."""

    parts: list[Part] = field(default_factory=list)

    @property
    def quantity(self) -> Decimal:
        return sum((part.quantity for part in self.parts), ZERO)

    @property
    def amount(self) -> Decimal:
        """What the known parts cost (an unknown part counts as nothing: see ``complete``)."""
        return sum((part.amount for part in self.parts if part.amount is not None), ZERO)

    @property
    def complete(self) -> bool:
        return all(part.price.known for part in self.parts)

    @property
    def exact(self) -> bool:
        return all(part.price.source in Source.EXACT for part in self.parts)

    @property
    def estimated_amount(self) -> Decimal:
        return sum(
            (part.amount for part in self.parts if part.amount is not None and part.price.source not in Source.EXACT),
            ZERO,
        )

    @property
    def unknown_quantity(self) -> Decimal:
        return sum((part.quantity for part in self.parts if not part.price.known), ZERO)


def money(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(CENT, rounding=ROUND_HALF_UP)


# --- Material prices -------------------------------------------------------------------------------


class PriceBook:
    """The prices known for materials: their priced receipt lines, and their standard prices."""

    def __init__(self):
        lines = (
            MaterialReceiptLine.objects.filter(receipt__is_cancelled=False, amount__gt=0, quantity__gt=0)
            .values_list("pk", "material_id", "receipt__date", "amount", "quantity")
            .order_by("receipt__date", "pk")
        )
        self.by_line: dict[int, Decimal] = {}
        self.dates: dict[int, list[datetime.date]] = defaultdict(list)
        self.prices: dict[int, list[Decimal]] = defaultdict(list)
        for pk, material_id, date, amount, quantity in lines:
            price = amount / quantity
            self.by_line[pk] = price
            self.dates[material_id].append(date)
            self.prices[material_id].append(price)
        self.standard = dict(Material.objects.filter(price__gt=0).values_list("pk", "price"))

    def at(self, material_id: int, date: datetime.date) -> Price:
        """An estimate for the material on ``date``: a receipt near it, else its standard price."""
        dates = self.dates.get(material_id)
        if dates:
            index = bisect.bisect_right(dates, date)
            return Price(self.prices[material_id][index - 1 if index else 0], Source.ESTIMATE)
        if material_id in self.standard:
            return Price(self.standard[material_id], Source.STANDARD)
        return UNKNOWN

    def of_lot(self, lot: traceability.Lot | None, material_id: int, date: datetime.date) -> Price:
        """The price of the delivery a part came from, or an estimate when it has none."""
        if lot is not None and lot.receipt_line_id in self.by_line:
            return Price(self.by_line[lot.receipt_line_id], Source.RECEIPT)
        return self.at(material_id, lot.date if lot is not None else date)


# --- Batches ---------------------------------------------------------------------------------------


@dataclass
class MaterialCost(Valued):
    line: RunLine | None = None


@dataclass
class BatchCost:
    run: ProductionRun
    materials: list[MaterialCost]

    @property
    def amount(self) -> Decimal:
        return sum((item.amount for item in self.materials), ZERO)

    @property
    def per_unit(self) -> Decimal | None:
        return self.amount / self.run.quantity if self.run.quantity and self.amount else None

    @property
    def complete(self) -> bool:
        return all(item.complete for item in self.materials)

    @property
    def exact(self) -> bool:
        return all(item.exact for item in self.materials)

    @property
    def estimated_amount(self) -> Decimal:
        return sum((item.estimated_amount for item in self.materials), ZERO)

    @property
    def missing(self) -> list[Material]:
        """Materials with no price at all: give them a receipt price or a standard price."""
        return [item.line.material for item in self.materials if not item.complete]


def batch_costs(runs, book: PriceBook | None = None) -> dict[int, BatchCost]:
    """{run id: its cost} for these production runs (one trace for all of them)."""
    runs = [run for run in runs if run.is_posted]
    if not runs:
        return {}
    book = book or PriceBook()
    lines = defaultdict(list)
    for line in RunLine.objects.filter(run__in=runs).select_related("material").order_by("position", "id"):
        lines[line.run_id].append(line)
    consumption = {
        (move.run_id, move.material_id): move
        for move in MaterialMove.objects.posted().filter(run__in=runs, kind=MaterialMove.Kind.CONSUMPTION)
    }
    traced = traceability.trace({material for _run, material in consumption}) if consumption else None
    result = {}
    for run in runs:
        items = []
        for line in lines[run.pk]:
            item = MaterialCost(line=line)
            move = consumption.get((run.pk, line.material_id))
            if move is not None and traced is not None:
                for piece in traced.pieces.get(move.pk, []):
                    item.parts.append(Part(piece.quantity, book.of_lot(piece.lot, line.material_id, run.date)))
            elif line.consumed:  # history: no stock movements, valued by what the norms wrote off
                item.parts.append(Part(line.consumed, book.at(line.material_id, run.date)))
            items.append(item)
        result[run.pk] = BatchCost(run=run, materials=items)
    return result


class UnitCosts:
    """Each product's cost per unit over time, from its batches: for valuing what was shipped."""

    def __init__(self, costs: dict[int, BatchCost]):
        self.by_run = {pk: cost.per_unit for pk, cost in costs.items()}
        self.exact_runs = {pk for pk, cost in costs.items() if cost.exact and cost.per_unit is not None}
        timeline = defaultdict(list)
        for cost in sorted(costs.values(), key=lambda cost: (cost.run.date, cost.run.pk)):
            if cost.per_unit is not None:
                timeline[cost.run.product_id].append((cost.run.date, cost.per_unit))
        self.dates = {product: [date for date, _cost in items] for product, items in timeline.items()}
        self.values = {product: [value for _date, value in items] for product, items in timeline.items()}

    def at(self, product_id: int, date: datetime.date) -> Price:
        dates = self.dates.get(product_id)
        if not dates:
            return UNKNOWN
        index = bisect.bisect_right(dates, date)
        return Price(self.values[product_id][index - 1 if index else 0], Source.ESTIMATE)

    def of_run(self, run_id: int | None, product_id: int, date: datetime.date) -> Price:
        if run_id is not None and self.by_run.get(run_id) is not None:
            source = Source.RECEIPT if run_id in self.exact_runs else Source.ESTIMATE
            return Price(self.by_run[run_id], source)
        return self.at(product_id, date)


def all_batch_costs(book: PriceBook | None = None) -> dict[int, BatchCost]:
    runs = ProductionRun.objects.posted().select_related("product")
    return batch_costs(list(runs), book)


# --- Shipments -------------------------------------------------------------------------------------


@dataclass
class LineMargin(Valued):
    """One shipment line: its amount, and what its units cost to make."""

    line: ProductShipmentLine | None = None
    shipment: ProductShipment | None = None

    @property
    def sign(self) -> int:
        return -1 if self.shipment.is_return else 1

    @property
    def revenue(self) -> Decimal:
        return self.sign * self.line.amount

    @property
    def units(self) -> int:
        return self.sign * self.line.quantity

    @property
    def cost(self) -> Decimal:
        return self.sign * self.amount

    @property
    def margin(self) -> Decimal:
        return self.revenue - self.cost


def shipment_margins(shipments, units: UnitCosts | None = None) -> list[LineMargin]:
    """Every line of these (posted) shipments with its cost, oldest batches shipped first."""
    shipments = [shipment for shipment in shipments if not shipment.is_cancelled]
    if not shipments:
        return []
    units = units or UnitCosts(all_batch_costs())
    lines = list(
        ProductShipmentLine.objects.filter(shipment__in=shipments).select_related(
            "product", "shipment__customer__parent"
        )
    )
    sources = {f"shipment:{line.shipment_id}:{line.pk}": line for line in lines}
    moves = {
        move.source: move
        for move in ProductMove.objects.posted().filter(source__in=list(sources), kind=ProductMove.Kind.SHIPMENT)
    }
    traced = traceability.trace_products({line.product_id for line in lines}) if moves else None
    result = []
    for line in lines:
        item = LineMargin(line=line, shipment=line.shipment)
        move = moves.get(f"shipment:{line.shipment_id}:{line.pk}")
        date = line.shipment.date
        if move is not None and traced is not None:
            for piece in traced.pieces.get(move.pk, []):
                run_id = piece.lot.move.run_id if piece.lot is not None else None
                item.parts.append(Part(piece.quantity, units.of_run(run_id, line.product_id, date)))
        else:  # history, or a return: valued at the product's cost per unit at the date
            item.parts.append(Part(Decimal(line.quantity), units.at(line.product_id, date)))
        result.append(item)
    return result


@dataclass
class MarginRow:
    """Shipment lines added up by product, customer or month."""

    key: object
    label: str
    units: int = 0
    revenue: Decimal = ZERO
    cost: Decimal = ZERO
    estimated: Decimal = ZERO
    unknown_units: Decimal = ZERO

    @property
    def cost_known(self) -> bool:
        """At least part of the cost is known (else cost and margin are shown as unknown, not as 0 and 100 %)."""
        return self.unknown_units < abs(self.units) or not self.units

    @property
    def margin(self) -> Decimal:
        return self.revenue - self.cost

    @property
    def percent(self) -> Decimal | None:
        if not self.revenue or not self.cost_known:
            return None
        return (self.margin / self.revenue * 100).quantize(Decimal("0.1"))

    @property
    def unit_cost(self) -> Decimal | None:
        return self.cost / self.units if self.units else None

    def add(self, item: LineMargin):
        self.units += item.units
        self.revenue += item.revenue
        self.cost += item.cost
        self.estimated += item.sign * item.estimated_amount
        self.unknown_units += item.unknown_quantity


def group_margins(items: list[LineMargin], by: str) -> list[MarginRow]:
    """Add up lines ``by`` "product", "customer" (its head company) or "month"; the largest amount first."""
    rows: dict[object, MarginRow] = {}
    for item in items:
        if by == "customer":
            company = item.shipment.customer
            head = company.parent if company.parent_id else company
            key, label = head.pk, head.name
        elif by == "month":
            key = item.shipment.date.replace(day=1)
            label = key
        else:
            key, label = item.line.product_id, item.line.product.name
        rows.setdefault(key, MarginRow(key=key, label=label)).add(item)
    if by == "month":
        return sorted(rows.values(), key=lambda row: row.key, reverse=True)
    return sorted(rows.values(), key=lambda row: row.revenue, reverse=True)


def total(rows: list[MarginRow]) -> MarginRow:
    result = MarginRow(key=None, label="")
    for row in rows:
        result.units += row.units
        result.revenue += row.revenue
        result.cost += row.cost
        result.estimated += row.estimated
        result.unknown_units += row.unknown_units
    return result


# --- Cost by product and month ---------------------------------------------------------------------


@dataclass
class ProductCost:
    """A product's batches in a period: units made, what they cost, per unit."""

    product: object
    batches: list[BatchCost] = field(default_factory=list)
    previous: Decimal | None = None  # cost per unit in the period before

    @property
    def units(self) -> int:
        return sum(cost.run.quantity for cost in self.batches)

    @property
    def amount(self) -> Decimal:
        return sum((cost.amount for cost in self.batches), ZERO)

    @property
    def per_unit(self) -> Decimal | None:
        return self.amount / self.units if self.units and self.amount else None

    @property
    def change(self) -> Decimal | None:
        """Change of the cost per unit against the period before, in %."""
        if self.per_unit is None or not self.previous:
            return None
        return ((self.per_unit - self.previous) / self.previous * 100).quantize(Decimal("0.1"))

    @property
    def complete(self) -> bool:
        return all(cost.complete for cost in self.batches)

    @property
    def estimated_amount(self) -> Decimal:
        return sum((cost.estimated_amount for cost in self.batches), ZERO)

    @property
    def missing(self) -> list[Material]:
        seen = {}
        for cost in self.batches:
            for material in cost.missing:
                seen[material.pk] = material
        return list(seen.values())

    def materials(self) -> list[dict]:
        """The product's cost by material, per unit, the dearest first: what its cost is made of."""
        rows: dict[int, dict] = {}
        units = self.units or 1
        for cost in self.batches:
            for item in cost.materials:
                material = item.line.material
                row = rows.setdefault(
                    material.pk, {"material": material, "quantity": ZERO, "amount": ZERO, "complete": True}
                )
                row["quantity"] += item.quantity
                row["amount"] += item.amount
                row["complete"] = row["complete"] and item.complete
        whole = self.amount or 1
        for row in rows.values():
            row["per_thousand"] = row["quantity"] / units * 1000  # norms read better per 1000 units
            row["per_unit"] = row["amount"] / units
            row["price"] = row["amount"] / row["quantity"] if row["quantity"] and row["amount"] else None
            row["share"] = (row["amount"] / whole * 100).quantize(Decimal("0.1"))
        return sorted(rows.values(), key=lambda row: (-row["amount"], row["material"].name))


def product_costs(start: datetime.date, end: datetime.date, previous_start: datetime.date) -> list[ProductCost]:
    """Each product made from ``start`` to ``end`` (inclusive), with its cost per unit then and the period before."""
    costs = all_batch_costs()
    current: dict[int, ProductCost] = {}
    before: dict[int, ProductCost] = {}
    for cost in sorted(costs.values(), key=lambda cost: (cost.run.date, cost.run.pk)):
        run = cost.run
        if start <= run.date <= end:
            current.setdefault(run.product_id, ProductCost(product=run.product)).batches.append(cost)
        elif previous_start <= run.date < start:
            before.setdefault(run.product_id, ProductCost(product=run.product)).batches.append(cost)
    for product_id, item in current.items():
        if product_id in before:
            item.previous = before[product_id].per_unit
    return sorted(current.values(), key=lambda item: (item.product.position, item.product.name))
