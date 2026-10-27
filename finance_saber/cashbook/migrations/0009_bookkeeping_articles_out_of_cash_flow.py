"""Opening balances and balance corrections move balances, but are not money coming in or going out."""

from django.db import migrations

# Same names as cashbook.models.BOOKKEEPING_ARTICLES (migrations don't import models).
BOOKKEEPING = ("Начальный остаток", "Корректировка остатка (+)", "Корректировка остатка (−)")


def mark(apps, schema_editor):
    apps.get_model("cashbook", "Article").objects.filter(name__in=BOOKKEEPING).update(in_cash_flow=False)


def unmark(apps, schema_editor):
    apps.get_model("cashbook", "Article").objects.filter(name__in=BOOKKEEPING).update(in_cash_flow=True)


class Migration(migrations.Migration):
    dependencies = [("cashbook", "0008_dashboard_fields")]

    operations = [migrations.RunPython(mark, unmark)]
