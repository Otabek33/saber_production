"""Batch traceability: which deliveries of raw materials went into which production batch.

A production batch is a product's run of one day (ProductionRun); its code,
"YYMMDD-NNN" (date and product), is what goes on the bottles. Its materials and
quantities are recorded with the run (RunLine), so they never change afterwards.

The lots are what came into the warehouse, as the stock ledger records it: each
receipt line (its supplier, document number and date), the opening balance, and
stock-count surpluses. Material use (production, write-offs, stock-count
shortages) is matched to the lots first in, first out: the oldest stock is used
first, and on the same day a delivery counts before the use. The matching is
worked out from the ledger whenever it is shown, never stored, so it can't drift
from the records it is made of; the same ledger always gives the same answer, and
a delivery entered late (back-dated) is placed where it belongs.

Two ways to trace:
* backward, from a batch (e.g. a complaint about a bottle): its materials and,
  for each, which deliveries it came from (batch_materials);
* forward, from a delivery (e.g. a supplier recalls a lot): every batch that used
  it, and how much is still in stock (lot_uses).

Finished goods are traced the same way (trace_products): a batch's output is a
lot, and shipments (documents and sales) take from the oldest batch first. So a
batch shows which customers got it (batch_destinations: a recall), and a shipment
which batches it carried (shipment_batches: a customer's complaint).
"""

import datetime
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from .models import (
    MaterialMove,
    MaterialReceipt,
    MaterialReceiptLine,
    ProductionRun,
    ProductMove,
    ProductShipment,
    RunLine,
)

ZERO = Decimal(0)


def batch_code(run: ProductionRun) -> str:
    """The code printed on the batch: production date and product, e.g. "261022-007"."""
    return f"{run.date:%y%m%d}-{run.product_id:03d}"


def parse_batch_code(text: str) -> tuple | None:
    """("261022-007") -> (date(2026, 10, 22), 7); None if it isn't a batch code."""
    code = text.strip()
    date_part, _sep, product_part = code.partition("-")
    if len(date_part) != 6 or not date_part.isdigit() or not product_part.isdigit():
        return None
    try:
        date = datetime.datetime.strptime(date_part, "%y%m%d").date()
    except ValueError:
        return None
    return date, int(product_part)


@dataclass
class Lot:
    """Something that came into stock: a receipt line, the opening balance or a stock-count surplus."""

    move: MaterialMove
    remaining: Decimal = ZERO
    receipt: MaterialReceipt | None = None
    receipt_line_id: int | None = None
    uses: list = field(default_factory=list)  # [(outflow move, quantity)]

    @property
    def date(self):
        return self.move.date

    @property
    def quantity(self) -> Decimal:
        return self.move.quantity

    @property
    def supplier(self):
        return self.receipt.supplier if self.receipt else None

    @property
    def run(self) -> ProductionRun | None:
        """For finished goods: the batch this lot is the output of."""
        return self.move.run if self.move.run_id else None

    @property
    def label(self) -> str:
        if self.receipt:
            return str(_("Receipt № %(number)s") % {"number": self.receipt.number})
        if self.run is not None:
            return str(_("Batch %(code)s") % {"code": batch_code(self.run)})
        if self.move.kind == MaterialMove.Kind.RECEIPT:
            return str(_("Receipt")) + (f": {self.move.note}" if self.move.note else "")
        return str(self.move.get_kind_display())


@dataclass
class Piece:
    """Part of a use that came from one lot; ``lot`` None: more was used than recorded stock."""

    lot: Lot | None
    quantity: Decimal


@dataclass
class Trace:
    """The ledger of some materials, matched first in, first out."""

    lots: list[Lot]
    pieces: dict[int, list[Piece]]  # outflow move id -> where it came from

    def lots_of_receipt(self, receipt: MaterialReceipt) -> list[Lot]:
        return [lot for lot in self.lots if lot.receipt is not None and lot.receipt.pk == receipt.pk]


def trace(material_ids) -> Trace:
    """Match every use of these materials to the lots it came from, first in, first out."""
    moves = list(
        MaterialMove.objects.posted()
        .filter(material_id__in=list(material_ids))
        .exclude(quantity=0)
        .select_related("run__product")
    )
    # Day by day; within a day what came in before what went out, then in the order entered.
    receipts = _receipts_of(moves)
    return _match(moves, "material_id", lambda move: receipts.get(move.pk, (None, None)))


def trace_products(product_ids) -> Trace:
    """Match every shipment (and write-off) of these products to the batches it came from, first in, first out."""
    moves = list(
        ProductMove.objects.posted()
        .filter(product_id__in=list(product_ids))
        .exclude(quantity=0)
        .select_related("run__product")
    )
    return _match(moves, "product_id", lambda move: (None, None))


def _match(moves, item_attr: str, receipt_of) -> Trace:
    """First in, first out, per item: each outflow takes from the oldest lots still open."""
    # Day by day; within a day what came in before what went out, then in the order entered.
    moves.sort(key=lambda move: (move.date, move.quantity < 0, move.pk))
    open_lots: dict[int, deque[Lot]] = defaultdict(deque)
    lots, pieces = [], {}
    for move in moves:
        if move.quantity > 0:
            receipt, line_id = receipt_of(move)
            lot = Lot(move=move, remaining=move.quantity, receipt=receipt, receipt_line_id=line_id)
            lots.append(lot)
            open_lots[getattr(move, item_attr)].append(lot)
            continue
        needed, taken = -move.quantity, []
        queue = open_lots[getattr(move, item_attr)]
        while needed > 0 and queue:
            lot = queue[0]
            part = min(lot.remaining, needed)
            lot.remaining -= part
            needed -= part
            lot.uses.append((move, part))
            taken.append(Piece(lot, part))
            if lot.remaining == 0:
                queue.popleft()
        if needed > 0:
            taken.append(Piece(None, needed))
        pieces[move.pk] = taken
    return Trace(lots=lots, pieces=pieces)


def _receipts_of(moves) -> dict[int, tuple]:
    """{move id: (receipt document, receipt line id)} for movements a receipt document made."""
    found = {}
    for move in moves:
        if move.source.startswith("receipt:"):
            parts = move.source.split(":")
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                found[move.pk] = (int(parts[1]), int(parts[2]))
    documents = MaterialReceipt.objects.select_related("supplier").in_bulk({doc for doc, _line in found.values()})
    return {pk: (documents.get(doc), line) for pk, (doc, line) in found.items() if doc in documents}


# --- Backward: a batch and where its materials came from --------------------------------------


@dataclass
class BatchMaterial:
    line: object  # RunLine: the material, its norm, defect and what was written off
    pieces: list[Piece]

    @property
    def traced(self) -> bool:
        return all(piece.lot is not None for piece in self.pieces)

    @property
    def norm(self) -> Decimal:
        """The norm per unit without trailing zeros: 0.0463500000 -> 0.04635, 10.0000000000 -> 10 (not 1E+1)."""
        return Decimal(format(self.line.quantity_per_unit.normalize(), "f"))


def batch_materials(run: ProductionRun) -> list[BatchMaterial]:
    """The batch's materials as recorded with it, each with the deliveries it came from."""
    lines = list(run.lines.select_related("material"))
    consumption = {
        move.material_id: move
        for move in MaterialMove.objects.posted().filter(run=run, kind=MaterialMove.Kind.CONSUMPTION)
    }
    traced = trace(consumption) if consumption else Trace([], {})
    result = []
    for line in lines:
        move = consumption.get(line.material_id)
        result.append(BatchMaterial(line=line, pieces=traced.pieces.get(move.pk, []) if move else []))
    return result


# --- Forward: a delivery and the batches that used it -----------------------------------------


@dataclass
class LotUse:
    """What became of one receipt line: the batches it went into, other uses, and what is left."""

    line: MaterialReceiptLine
    lot: Lot | None
    batches: list[tuple[ProductionRun, Decimal]] = field(default_factory=list)
    other: list[tuple[MaterialMove, Decimal]] = field(default_factory=list)  # write-offs, count shortages

    @property
    def remaining(self) -> Decimal:
        return self.lot.remaining if self.lot else ZERO

    @property
    def used(self) -> Decimal:
        return self.line.quantity - self.remaining


def lot_uses(receipt: MaterialReceipt) -> list[LotUse]:
    """For each line of a receipt: the batches its material went into, other uses, and what is left."""
    lines = list(receipt.lines.select_related("material"))
    traced = trace({line.material_id for line in lines}) if lines else Trace([], {})
    lots = {lot.receipt_line_id: lot for lot in traced.lots_of_receipt(receipt)}
    result = []
    for line in lines:
        use = LotUse(line=line, lot=lots.get(line.pk))
        for move, quantity in use.lot.uses if use.lot else []:
            if move.run_id:
                use.batches.append((move.run, quantity))
            else:
                use.other.append((move, quantity))
        result.append(use)
    return result


def runs_using(receipts) -> set[int]:
    """Ids of the production runs that used material of any of these receipts (one trace for all)."""
    receipt_ids = {receipt.pk for receipt in receipts}
    lines = MaterialReceiptLine.objects.filter(receipt_id__in=receipt_ids)
    traced = trace(set(lines.values_list("material_id", flat=True)))
    return {
        move.run_id
        for lot in traced.lots
        if lot.receipt is not None and lot.receipt.pk in receipt_ids
        for move, _quantity in lot.uses
        if move.run_id
    }


def register_rows(runs) -> list[dict]:
    """One row per batch, material and lot: the traceability register (its Excel file)."""
    runs = list(runs)
    lines = defaultdict(list)
    for line in RunLine.objects.filter(run__in=runs).select_related("material").order_by("position", "id"):
        lines[line.run_id].append(line)
    consumption = {
        (move.run_id, move.material_id): move
        for move in MaterialMove.objects.posted().filter(run__in=runs, kind=MaterialMove.Kind.CONSUMPTION)
    }
    traced = trace({material_id for _run, material_id in consumption}) if consumption else Trace([], {})
    rows = []
    for run in runs:
        for line in lines[run.pk]:
            move = consumption.get((run.pk, line.material_id))
            for piece in (traced.pieces.get(move.pk) if move else None) or [None]:
                rows.append({"run": run, "line": line, "piece": piece})
    return rows


# --- Finished goods: which customers got a batch, which batches a shipment carried ----------------


@dataclass
class Destination:
    """Where an outflow of finished goods went: a customer and the document, or what else it was."""

    move: ProductMove
    quantity: Decimal
    customer: str = ""
    document: str = ""
    shipment: ProductShipment | None = None


def destinations(moves_and_quantities) -> list[Destination]:
    """Describe outflows: a shipment document's customer, or the movement's own note."""
    pairs = list(moves_and_quantities)
    shipment_ids = set()
    for move, _quantity in pairs:
        kind, _sep, rest = move.source.partition(":")
        if kind == "shipment" and rest.split(":")[0].isdigit():
            shipment_ids.add(int(rest.split(":")[0]))
    shipments = ProductShipment.objects.select_related("customer").in_bulk(shipment_ids)
    result = []
    for move, quantity in pairs:
        kind, _sep, rest = move.source.partition(":")
        item = Destination(move=move, quantity=quantity)
        if kind == "shipment" and (shipment := shipments.get(int(rest.split(":")[0]))):
            item.customer, item.shipment = shipment.customer.name, shipment
            item.document = str(_("Shipment № %(number)s") % {"number": shipment.number})
        else:
            item.customer, item.document = move.note, str(move.get_kind_display())
        result.append(item)
    return result


@dataclass
class BatchShipping:
    """What became of a batch's output: where it went, and what is still in stock."""

    lot: Lot | None
    destinations: list[Destination] = field(default_factory=list)

    @property
    def remaining(self) -> Decimal:
        return self.lot.remaining if self.lot else ZERO


def batch_destinations(run: ProductionRun) -> BatchShipping:
    """The customers (and other outflows) a posted batch's output went to, oldest stock shipped first."""
    output = ProductMove.objects.posted().filter(run=run, kind=ProductMove.Kind.OUTPUT).first()
    if output is None:
        return BatchShipping(lot=None)
    lot = next((lot for lot in trace_products([run.product_id]).lots if lot.move.pk == output.pk), None)
    return BatchShipping(lot=lot, destinations=destinations(lot.uses) if lot else [])


def shipment_batches(shipment: ProductShipment) -> dict[int, list[Piece]]:
    """{shipment line id: the batches (lots) its products came from}, oldest stock shipped first."""
    lines = list(shipment.lines.all())
    if not lines or shipment.is_cancelled:
        return {}
    traced = trace_products({line.product_id for line in lines})
    moves = {move.source: move.pk for move in shipment.moves()}
    return {line.pk: traced.pieces.get(moves.get(f"{shipment.stock_source}{line.pk}"), []) for line in lines}
