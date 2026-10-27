"""Add the "Инкасса & Розница" desk (UZS cash only) and its handbook.

The articles that existed so far were all for "Касса БОН", so they are limited to
that desk, except the shared "Не распределено" / "Корректировка остатка" ones.
Categories of "Дист расход" follow the notes used on the workbook's
"Инкасса & Розница" sheet (стоянка, арава, клик, ...).
"""

from django.db import migrations

SHARED_PREFIXES = ("Не распределено", "Корректировка остатка")

INKASSA_HANDBOOK = {
    "income": [
        ("Приход розница", ["Приход розница"]),
    ],
    "expense": [
        (
            "Дист расход",
            [
                "Стоянка",
                "Арава",
                "Клик",
                "Брак",
                "Питание",
                "Карз и долг",
                "Картридж и печать",
                "Топливо",
                "Машина",
                "Корректировка",
                "Прочие",
            ],
        ),
        ("Инкассо", ["Инкассо"]),
    ],
}


def forwards(apps, schema_editor):
    CashDesk = apps.get_model("cashbook", "CashDesk")
    Article = apps.get_model("cashbook", "Article")
    Category = apps.get_model("cashbook", "Category")

    bon = CashDesk.objects.get(slug="bon")
    bon.position = 1
    bon.save(update_fields=["position"])
    inkassa, _ = CashDesk.objects.get_or_create(
        slug="inkassa", defaults={"name": "Инкасса & Розница", "accounts": ["cash_uzs"], "position": 2}
    )

    for article in Article.objects.all():
        if not article.name.startswith(SHARED_PREFIXES):
            article.desks.add(bon)

    for kind, articles in INKASSA_HANDBOOK.items():
        for position, (article_name, categories) in enumerate(articles, start=1):
            article, _ = Article.objects.get_or_create(name=article_name, defaults={"kind": kind, "position": position})
            article.desks.add(inkassa)
            for category_name in categories:
                Category.objects.get_or_create(article=article, name=category_name)

    # Shared article for a desk's opening balance (e.g. when its history starts mid-way).
    opening, _ = Article.objects.get_or_create(name="Начальный остаток", defaults={"kind": "income", "position": 0})
    Category.objects.get_or_create(article=opening, name="Начальный остаток")


class Migration(migrations.Migration):
    dependencies = [("cashbook", "0004_desk_accounts_and_article_desks")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
