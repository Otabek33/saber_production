"""Materials get their official names (production/material_names.py), in place.

A renamed material keeps its stock, deliveries, production history and recipe norms.
The flavoured 0,45 l Energy cans are merged into one can. Materials not on the list
are taken out of the recipes; they are deleted unless history refers to them, then
made inactive. Official materials still missing are added. A new, empty database is
left alone: it gets the materials from the project's files (production/seed/).
"""

from django.db import migrations

from finance_saber.production.material_names import OFFICIAL, RENAMED, RETIRED

# Rows naming the material once per document: merging two materials adds up their rows.
PER_DOCUMENT = [
    ("RecipeLine", "product", ["quantity_per_unit"]),
    ("RunLine", "run", ["quantity_per_unit", "defect", "consumed"]),
    ("MaterialReceiptLine", "receipt", ["quantity"]),
]


def merge(apps, source, target):
    """Move everything of ``source`` to ``target``, then delete ``source``."""
    for model_name, owner, amounts in PER_DOCUMENT:
        model = apps.get_model("production", model_name)
        for row in model.objects.filter(material=source):
            existing = model.objects.filter(material=target, **{owner: getattr(row, owner)}).first()
            if existing is None:
                row.material = target
                row.save(update_fields=["material"])
                continue
            for amount in amounts:
                setattr(existing, amount, getattr(existing, amount) + getattr(row, amount))
            existing.save(update_fields=amounts)
            row.delete()
    apps.get_model("production", "MaterialMove").objects.filter(material=source).update(material=target)
    source.delete()


def official_names(apps, schema_editor):
    Material = apps.get_model("production", "Material")
    if not Material.objects.exists():
        return
    by_name = {material.name: material for material in Material.objects.all()}
    for old, new in RENAMED.items():
        material = by_name.pop(old, None)
        if material is None:
            continue
        target = by_name.get(new)
        if target is None:
            material.name = new
            material.save(update_fields=["name"])
            by_name[new] = material
        else:
            merge(apps, material, target)

    RecipeLine = apps.get_model("production", "RecipeLine")
    for name in RETIRED:
        material = by_name.get(name)
        if material is None:
            continue
        RecipeLine.objects.filter(material=material).delete()
        if material.moves.exists() or material.run_lines.exists() or material.receipt_lines.exists():
            material.is_active = False
            material.save(update_fields=["is_active"])
        else:
            material.delete()

    for name, unit, kind in OFFICIAL:
        material = by_name.get(name)
        if material is None:
            Material.objects.create(name=name, unit=unit, kind=kind)
        elif (material.unit, material.kind, material.is_active) != (unit, kind, True):
            material.unit, material.kind, material.is_active = unit, kind, True
            material.save(update_fields=["unit", "kind", "is_active"])


class Migration(migrations.Migration):
    dependencies = [("production", "0008_product_shipments")]

    operations = [migrations.RunPython(official_names, migrations.RunPython.noop)]
