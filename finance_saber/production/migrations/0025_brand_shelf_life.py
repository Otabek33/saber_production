"""Shelf life of the products already loaded, by brand, counted from the production date.

Be Fresh 24 months, 7 Energy 18 months, Sakura teas 12 months. A product is matched by
its production or sales name (case does not matter); others are left as they are. Running
it again changes nothing. The reverse keeps the values: there is nothing to undo safely.
"""

from django.db import migrations

# (words in the name or sales name, shelf life as expiry.parse reads it)
BRANDS = (
    (("fresh",), "24 месяца"),
    (("energy",), "18 месяцев"),
    (("sakura", "green tea", "blak tea", "black tea"), "12 месяцев"),
)


def brand_shelf_life(name: str) -> str | None:
    text = name.casefold()
    for words, shelf_life in BRANDS:
        if any(word in text for word in words):
            return shelf_life
    return None


def set_shelf_life(apps, schema_editor):
    Product = apps.get_model("production", "Product")
    for product in Product.objects.all():
        shelf_life = brand_shelf_life(f"{product.name} {product.sales_name}")
        if shelf_life and product.shelf_life != shelf_life:
            product.shelf_life = shelf_life
            product.save(update_fields=["shelf_life"])


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0024_material_price_currency"),
    ]

    operations = [
        migrations.RunPython(set_shelf_life, migrations.RunPython.noop),
    ]
