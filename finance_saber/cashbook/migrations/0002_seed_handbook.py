"""Seed the "Касса БОН" desk and the handbook used in the finance workbook.

Articles and categories are the ones used on the workbook's "Касса Бон" sheet
(columns B and C), in the order of its "Сash flow" sheet. Edit them later on
the Handbook page; this migration only adds what is missing.
"""

from django.db import migrations

HANDBOOK = {
    "income": [
        ("Продажа БОН", ["Продажа БОН", "Прочие поступления UZB"]),
        ("Продажа Distr", ["Продажа Distr"]),
        ("Прочие поступления UZB", ["Прочие поступления UZB"]),
        ("Прочие поступления USD", ["Прочие поступления USD"]),
    ],
    "transfer": [
        ("Переброска на карту", ["Переброска на карту"]),
        ("Обмена валюты", ["Обмена валюты"]),
    ],
    "expense": [
        ("Взаиморасчеты с персоналом", ["ЗП", "Долг", "Под отчёт"]),
        ("Транспорт", ["Расходы на машину", "Транспорт", "Такси", "Машина Чанган", "Ремонт"]),
        ("Прочие расходы", ["Прочие", "Офис расход", "Услуга (Click)"]),
        ("Питание", ["Питание"]),
        ("Банковские услуги", ["Комиссия банка"]),
        ("Маркетинг и реклама", ["Маркетинг", "Бонус программа"]),
        ("Аренда", ["Аренда"]),
        ("Командировка", ["Командировка"]),
        ("Дивиденд", ["Дивиденд"]),
        ("Импорт поставщики", ["Импорт поставщики"]),
        ("Импорт транспорт", ["Импорт транспорт"]),
        ("Логистика импорт", ["Прочие"]),
        ("Дистр", ["Дистр"]),
        ("Сахир", ["Прочие"]),
        ("Чанган", ["Прочие"]),
    ],
}


def seed(apps, schema_editor):
    CashDesk = apps.get_model("cashbook", "CashDesk")
    Article = apps.get_model("cashbook", "Article")
    Category = apps.get_model("cashbook", "Category")

    CashDesk.objects.get_or_create(slug="bon", defaults={"name": "Касса БОН"})
    for kind, articles in HANDBOOK.items():
        for position, (article_name, categories) in enumerate(articles, start=1):
            article, _ = Article.objects.get_or_create(
                name=article_name, defaults={"kind": kind, "position": position}
            )
            for category_name in categories:
                Category.objects.get_or_create(article=article, name=category_name)


class Migration(migrations.Migration):
    dependencies = [("cashbook", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
