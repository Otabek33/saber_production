"""How much can be produced from a material: the analysis page's arithmetic.

For one material and an amount of it (its stock by default, or any figure to try), every
active product whose recipe uses it gets:

* how many units (cans) that amount is enough for: amount ÷ the norm per unit;
* how many units can really be made once every other material of the recipe is counted
  too (at its current stock), and which material runs out first.

Like Recipes and norms › units possible, norms are per finished unit. Planning with a
margin, a norm may be raised by the recipe line's allowed loss (% of the use).
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from .models import Material, Product, RecipeLine, material_stock

ZERO = Decimal(0)


@dataclass
class ProductCapacity:
    product: Product
    norm: Decimal  # of the chosen material per unit, with the margin if asked
    from_material: int  # units the chosen amount is enough for
    possible: int  # units every material of the recipe allows together
    bottleneck: Material | None  # the material that runs out first (None: the chosen one, or no recipe)
    bottleneck_stock: Decimal = ZERO

    @property
    def litres(self) -> Decimal:
        return (self.product.volume * self.from_material).quantize(Decimal("0.1"))

    @property
    def possible_litres(self) -> Decimal:
        return (self.product.volume * self.possible).quantize(Decimal("0.1"))

    @property
    def limited_by_other(self) -> bool:
        return self.bottleneck is not None and self.possible < self.from_material


def norm_of(line: RecipeLine, with_loss: bool) -> Decimal:
    return line.quantity_per_unit * (1 + line.allowed_loss / 100) if with_loss else line.quantity_per_unit


def units(amount: Decimal, norm: Decimal) -> int:
    return math.floor(max(amount, ZERO) / norm) if norm else 0


def capacity(material: Material, amount: Decimal, with_loss: bool = False) -> list[ProductCapacity]:
    """Every active product using ``material``, with what ``amount`` of it is enough for; most units first."""
    stock = material_stock()
    stock[material.pk] = amount
    products = (
        Product.objects.filter(is_active=True, recipe__material=material)
        .distinct()
        .prefetch_related("recipe__material")
    )
    rows = []
    for product in products:
        lines = list(product.recipe.all())
        chosen = next(line for line in lines if line.material_id == material.pk)
        norm = norm_of(chosen, with_loss)
        from_material = units(amount, norm)
        possible, bottleneck = from_material, None
        for line in lines:
            if line.material_id == material.pk:
                continue
            allowed = units(stock.get(line.material_id, ZERO), norm_of(line, with_loss))
            if allowed < possible:
                possible, bottleneck = allowed, line.material
        rows.append(
            ProductCapacity(
                product=product,
                norm=norm,
                from_material=from_material,
                possible=possible,
                bottleneck=bottleneck,
                bottleneck_stock=stock.get(bottleneck.pk, ZERO) if bottleneck else ZERO,
            )
        )
    return sorted(rows, key=lambda row: (-row.from_material, row.product.position, row.product.name))
