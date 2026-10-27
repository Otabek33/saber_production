"""Add the "Банк" desk, the articles bank statement lines use, and the sales regions.

Articles and regions are the ones on the workbook's "Банк" sheet and its Handbook
(columns H:I and Y:AA). Articles seen in both directions there (e.g. "Месный
продажа": sales and their refunds) are of type "both". Each bank article gets a
category of the same name, which bank lines use.
"""

from django.db import migrations

BANK_ARTICLES = {
    "both": ["Месный продажа", "Местные поставщики", "Взаиморасчеты с офисом"],
    "income": ["Денежные поступления от экспорта"],
    "expense": [
        "Гос Бюджет",
        "Финансовые расходы",
        "Маркетинг и реклама",
        "Импорт поставщики",
        "Взаиморасчеты с персоналом",
        "Прочие расходы",
        "Аренда",
    ],
}

REGIONS = [
    "KEY ACCOUNT",
    "Tash reg",
    "CHIRCHIQ",
    "Samarqand",
    "Bukhara",
    "Namangan",
    "Andijon",
    "Fargona",
    "Kokand",
    "Shahrisabz",
    "Qashqadaryo",
    "Sirdaryo",
    "Surxondaryo",
    "Xorazm",
    "Qaraqalpaq",
]


def forwards(apps, schema_editor):
    CashDesk = apps.get_model("cashbook", "CashDesk")
    Article = apps.get_model("cashbook", "Article")
    Category = apps.get_model("cashbook", "Category")
    Region = apps.get_model("cashbook", "Region")

    bank, _ = CashDesk.objects.get_or_create(
        slug="bank", defaults={"name": "Банк", "kind": "bank", "accounts": ["bank"], "position": 3}
    )
    for kind, names in BANK_ARTICLES.items():
        for position, name in enumerate(names, start=1):
            # Some exist already for "Касса БОН"; they keep their type and also become usable on the bank.
            article, _ = Article.objects.get_or_create(name=name, defaults={"kind": kind, "position": position})
            article.desks.add(bank)
            Category.objects.get_or_create(article=article, name=name)

    for name in REGIONS:
        Region.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [("cashbook", "0006_bank")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
