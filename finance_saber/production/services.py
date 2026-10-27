"""Production operations: record a day's output, check materials, count stock."""

import math
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from .models import (
    Material,
    MaterialMove,
    Product,
    ProductionRun,
    ProductMove,
    RecipeLine,
    RunLine,
    material_stock,
    product_stock,
    round_quantity,
)


@dataclass
class MaterialNeed:
    material: Material
    needed: Decimal  # for the quantities being entered
    stock_after: Decimal  # stock once they are recorded

    @property
    def is_short(self) -> bool:
        return self.stock_after < 0


def recipe_lines(product: Product, run: ProductionRun | None = None) -> list[RunLine]:
    """The recipe a run of ``product`` uses: the run's own if it has one, else the product's current recipe."""
    if run is not None and run.pk:
        lines = list(run.lines.select_related("material"))
        if lines:
            return lines
    return [
        RunLine(
            material=line.material,
            quantity_per_unit=line.quantity_per_unit,
            allowed_loss=line.allowed_loss,
            position=line.position,
        )
        for line in product.recipe.select_related("material")
    ]


def material_needs(quantities: dict[Product, int], date=None) -> list[MaterialNeed]:
    """Materials the given production uses, and the stock left after it.

    A product already made that day uses that run's own recipe, defect and brewed
    quantity; others use the product's current recipe. Re-saving a day replaces its
    runs, so what that day's runs already consumed is added back before
    subtracting the new needs.
    """
    needs: dict[int, Decimal] = {}
    runs = {}
    if date:
        runs = {run.product_id: run for run in ProductionRun.objects.filter(date=date, product__in=quantities)}
    for product, quantity in quantities.items():
        if not quantity:
            continue
        saved = runs.get(product.pk)
        planned = ProductionRun(
            product=product, quantity=quantity, batch_quantity=saved.batch_quantity if saved else None
        )
        for line in recipe_lines(product, saved):
            needs[line.material_id] = needs.get(line.material_id, Decimal(0)) + line.consumption(planned)
    if not needs:
        return []
    stock = material_stock()
    already = {}
    if date:
        already = MaterialMove.objects.filter(run__date=date, run__product__in=quantities).stock_by("material_id")
    materials = Material.objects.in_bulk(needs)
    return [
        MaterialNeed(
            material=materials[pk],
            needed=needed,
            stock_after=stock.get(pk, Decimal(0)) - already.get(pk, Decimal(0)) - needed,
        )
        for pk, needed in sorted(needs.items(), key=lambda item: materials[item[0]].name)
    ]


@transaction.atomic
def save_day(date, quantities: dict[Product, int], user=None) -> int:
    """Record each product's output for ``date`` (0 cancels it). Returns how many runs changed."""
    changed = 0
    runs = {run.product_id: run for run in ProductionRun.objects.filter(date=date, product__in=quantities)}
    for product, quantity in quantities.items():
        run = runs.get(product.pk)
        if run is None:
            if not quantity:
                continue
            run = ProductionRun(date=date, product=product, quantity=quantity, created_by=user)
        elif run.quantity == quantity and run.is_cancelled == (not quantity):
            continue
        else:
            run.quantity = quantity or run.quantity
            run.is_cancelled = not quantity
        run.save()
        run.rebuild_moves()
        changed += 1
    return changed


@dataclass
class CalculationLine:
    """A line of the production calculation as entered: the norm is per unit (cans)."""

    material: Material
    quantity_per_unit: Decimal
    defect: Decimal = Decimal(0)
    allowed_loss: Decimal = Decimal(2)


@transaction.atomic
def save_calculation(
    date,
    product: Product,
    quantity: int,
    batch_quantity: int | None,
    lines: list[CalculationLine],
    user=None,
    update_recipe: bool = True,
) -> tuple[ProductionRun, bool]:
    """Record a product's production for a day with the recipe it was made with.

    The run's own recipe is replaced by ``lines``. If this is the product's latest
    production, its recipe becomes these norms too, so the next production starts
    from them; correcting an older day leaves the current recipe alone. Returns the
    run and whether the product's recipe was updated.
    """
    run = ProductionRun.objects.filter(date=date, product=product).first()
    if run is None:
        run = ProductionRun(date=date, product=product, created_by=user)
    run.quantity = quantity
    run.batch_quantity = batch_quantity if batch_quantity and batch_quantity != quantity else None
    run.is_cancelled = False
    run.save()
    run.lines.all().delete()
    RunLine.objects.bulk_create(
        RunLine(
            run=run,
            material=line.material,
            quantity_per_unit=line.quantity_per_unit,
            defect=line.defect,
            allowed_loss=line.allowed_loss,
            position=position,
        )
        for position, line in enumerate(lines, start=1)
    )
    run.rebuild_moves()

    is_latest = not product.runs.filter(is_cancelled=False, date__gt=date).exists()
    if not (update_recipe and is_latest):
        return run, False
    current = {line.material_id: line for line in product.recipe.all()}
    wanted = {line.material.pk: (position, line) for position, line in enumerate(lines, start=1)}
    product.recipe.exclude(material_id__in=wanted).delete()
    for material_id, (position, line) in wanted.items():
        recipe_line = current.get(material_id) or RecipeLine(product=product, material=line.material)
        recipe_line.quantity_per_unit = line.quantity_per_unit or Decimal("0.0000000001")
        recipe_line.allowed_loss = line.allowed_loss
        recipe_line.position = position
        recipe_line.save()
    return run, True


def units_possible(products) -> dict[int, int]:
    """{product id: how many units current material stock allows}, by each product's recipe."""
    stock = material_stock()
    result = {}
    for product in products:
        limits = [
            math.floor(max(stock.get(line.material_id, Decimal(0)), Decimal(0)) / line.quantity_per_unit)
            for line in product.recipe.all()
        ]
        result[product.pk] = min(limits) if limits else 0
    return result


def count_stock(item, counted: Decimal, date, note="", user=None):
    """Record a stock count: a movement for the difference between the books and ``counted``."""
    if isinstance(item, Material):
        current = material_stock().get(item.pk, Decimal(0))
        model, field = MaterialMove, "material"
    else:
        current = product_stock().get(item.pk, Decimal(0))
        model, field = ProductMove, "product"
    return model.objects.create(
        **{field: item},
        date=date,
        kind=model.Kind.ADJUSTMENT,
        quantity=round_quantity(counted - current),
        counted=counted,
        note=note,
        created_by=user,
    )
