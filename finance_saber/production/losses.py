"""Norms against actual use: where materials are lost, and why.

For a period, per material, from the records (nothing stored):

* By the norm: what the posted production runs should have used (RunLine.norm_use).
* Allowed: the loss the runs' norms allow on top (each line's allowed loss, % of its norm use).
* Losses, each kept apart because each has its own cause:
  - production defects (RunLine.defect), with their reasons;
  - write-offs (spoiled, damaged in the warehouse: their note says why);
  - stock-count differences: a shortage found by a count is material used or lost without a
    record; a surplus counts against it (net shortage, never below zero overall).
* Over the norm = the losses; over the allowed = losses − allowed. A material losing more
  than allowed is what to look at first.

Values are in UZS at the material's price for the period (costing.PriceBook: the latest
receipt up to the period's end, else its standard price), so materials in different units
can be compared and the dearest losses come first.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from . import costing
from .models import Material, MaterialMove, ProductionRun, RunLine

ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass
class MaterialLoss:
    material: Material
    norm: Decimal = ZERO  # use by the norm
    allowed: Decimal = ZERO  # loss the norms allow
    defect: Decimal = ZERO
    write_off: Decimal = ZERO
    shortage: Decimal = ZERO  # net stock-count shortage (surplus negative)
    reasons: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    price: costing.Price = costing.UNKNOWN

    @property
    def losses(self) -> Decimal:
        return self.defect + self.write_off + max(self.shortage, ZERO)

    @property
    def actual(self) -> Decimal:
        return self.norm + self.defect + self.write_off + self.shortage

    @property
    def percent(self) -> Decimal | None:
        """Losses as % of the use by the norm."""
        return (self.losses / self.norm * HUNDRED).quantize(Decimal("0.1")) if self.norm else None

    @property
    def allowed_percent(self) -> Decimal | None:
        return (self.allowed / self.norm * HUNDRED).quantize(Decimal("0.1")) if self.norm else None

    @property
    def over_allowed(self) -> Decimal:
        return self.losses - self.allowed

    @property
    def too_much(self) -> bool:
        return self.losses > 0 and self.over_allowed > 0

    def value(self, quantity: Decimal) -> Decimal | None:
        return quantity * self.price.value if self.price.known else None

    @property
    def losses_value(self) -> Decimal | None:
        return self.value(self.losses)

    @property
    def over_allowed_value(self) -> Decimal | None:
        return self.value(max(self.over_allowed, ZERO))


@dataclass
class ReasonLoss:
    reason: str
    label: str
    entries: int = 0
    value: Decimal = ZERO
    unpriced: int = 0  # entries of materials without a price
    materials: dict[int, list] = field(default_factory=dict)  # {material id: [material, quantity]}

    @property
    def top(self) -> list[tuple[Material, Decimal]]:
        """The materials most scrapped for this reason (by quantity, in their own units), at most three."""
        items = sorted(self.materials.values(), key=lambda pair: -pair[1])
        return [(material, quantity) for material, quantity in items[:3]]


@dataclass
class Report:
    start: datetime.date
    end: datetime.date
    materials: list[MaterialLoss]
    reasons: list[ReasonLoss]
    runs: int

    @property
    def losses_value(self) -> Decimal:
        return sum((row.losses_value or ZERO for row in self.materials), ZERO)

    @property
    def over_allowed_value(self) -> Decimal:
        return sum((row.over_allowed_value or ZERO for row in self.materials), ZERO)

    @property
    def too_much(self) -> list[MaterialLoss]:
        return [row for row in self.materials if row.too_much]

    @property
    def unpriced(self) -> int:
        return sum(1 for row in self.materials if row.losses and not row.price.known)


def report(start: datetime.date, end: datetime.date, book: costing.PriceBook | None = None) -> Report:
    """Norms, allowed loss and losses per material from ``start`` to ``end`` (inclusive)."""
    book = book or costing.PriceBook()
    rows: dict[int, MaterialLoss] = {}
    materials = Material.objects.in_bulk()

    def row(material_id: int) -> MaterialLoss:
        if material_id not in rows:
            rows[material_id] = MaterialLoss(material=materials[material_id], price=book.at(material_id, end))
        return rows[material_id]

    runs = list(ProductionRun.objects.posted().filter(date__gte=start, date__lte=end))
    by_run = {run.pk: run for run in runs}
    reasons: dict[str, ReasonLoss] = {}
    labels = dict(RunLine.DefectReason.choices)
    for line in RunLine.objects.filter(run__in=runs).select_related("material"):
        run = by_run[line.run_id]
        item = row(line.material_id)
        norm = line.norm_use(run)
        item.norm += norm
        item.allowed += norm * line.allowed_loss / HUNDRED
        if line.defect:
            item.defect += line.defect
            reason = line.defect_reason or ""
            item.reasons[reason] += line.defect
            group = reasons.setdefault(reason, ReasonLoss(reason, labels.get(reason, "")))
            group.entries += 1
            group.materials.setdefault(line.material_id, [line.material, ZERO])[1] += line.defect
            if item.price.known:
                group.value += line.defect * item.price.value
            else:
                group.unpriced += 1
    moves = MaterialMove.objects.posted().filter(
        date__gte=start, date__lte=end, kind__in=[MaterialMove.Kind.WRITE_OFF, MaterialMove.Kind.ADJUSTMENT]
    )
    for move in moves:
        item = row(move.material_id)
        if move.kind == MaterialMove.Kind.WRITE_OFF:
            item.write_off -= move.quantity  # written off: negative quantities
        else:
            item.shortage -= move.quantity  # a count's shortage is negative, a surplus positive
    ordered = sorted(
        (item for item in rows.values() if item.norm or item.losses or item.shortage),
        # Over the allowed loss first, the dearest first (by % where there is no price).
        key=lambda item: (
            not item.too_much,
            -(item.over_allowed_value or ZERO),
            -(item.losses_value or ZERO),
            -(item.percent or ZERO),
        ),
    )
    return Report(
        start=start,
        end=end,
        materials=ordered,
        reasons=sorted(reasons.values(), key=lambda group: (-group.value, -group.entries)),
        runs=len(runs),
    )
