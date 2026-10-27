"""Give every existing production run its own recipe (RunLine).

A run that wrote off materials gets the norms it actually used (consumed ÷ units),
so its stock movements stay exactly as they are. A run without consumption
(imported history) gets a copy of its product's current recipe.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations
from django.db.models import Sum

NORM_STEP = Decimal("0.0000000001")
QUANTITY_STEP = Decimal("0.0001")


def copy_recipes(apps, schema_editor):
    ProductionRun = apps.get_model("production", "ProductionRun")
    RecipeLine = apps.get_model("production", "RecipeLine")
    RunLine = apps.get_model("production", "RunLine")
    MaterialMove = apps.get_model("production", "MaterialMove")

    for run in ProductionRun.objects.filter(lines__isnull=True).iterator():
        used = {
            row["material_id"]: -row["total"]
            for row in MaterialMove.objects.filter(run=run, kind="consumption")
            .values("material_id")
            .annotate(total=Sum("quantity"))
        }
        recipe = {line.material_id: line for line in RecipeLine.objects.filter(product_id=run.product_id)}
        lines = []
        if used and run.quantity:
            for position, (material_id, consumed) in enumerate(used.items(), start=1):
                norm = (consumed / run.quantity).quantize(NORM_STEP, rounding=ROUND_HALF_UP)
                recipe_line = recipe.get(material_id)
                lines.append(
                    RunLine(
                        run=run,
                        material_id=material_id,
                        quantity_per_unit=norm,
                        consumed=consumed,
                        allowed_loss=recipe_line.allowed_loss if recipe_line else Decimal(2),
                        position=recipe_line.position if recipe_line else position,
                    )
                )
        else:
            for line in recipe.values():
                lines.append(
                    RunLine(
                        run=run,
                        material_id=line.material_id,
                        quantity_per_unit=line.quantity_per_unit,
                        consumed=(line.quantity_per_unit * run.quantity).quantize(QUANTITY_STEP, ROUND_HALF_UP),
                        allowed_loss=line.allowed_loss,
                        position=line.position,
                    )
                )
        RunLine.objects.bulk_create(lines)


class Migration(migrations.Migration):
    dependencies = [("production", "0004_run_recipe")]

    operations = [migrations.RunPython(copy_recipes, migrations.RunPython.noop)]
