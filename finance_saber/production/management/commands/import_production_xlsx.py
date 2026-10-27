import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from finance_saber.production.importer import SheetError, import_workbooks, read_workbooks


class Command(BaseCommand):
    help = (
        "Import production from the workbooks: materials with their stock (report workbook, sheet "
        "Сбор, columns W–AA), recipes (the same sheet's recipe blocks), products and past output "
        "(finance workbook, sheet Warehouse). Safe to re-run: existing materials, products, "
        "recipes and opening stock are left as they are. The same import is on the Production › "
        "Import page, for servers where the files aren't on disk."
    )

    def add_arguments(self, parser):
        parser.add_argument("recipes", type=Path, help="Report workbook with the Сбор sheet")
        parser.add_argument("warehouse", type=Path, help="Finance workbook with the Warehouse sheet")
        parser.add_argument(
            "--stock-date",
            default=None,
            help="Date the Сбор stock column was counted, YYYY-MM-DD (default: today)",
        )
        parser.add_argument("--user", help="Username to record as the author")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported, save nothing")

    def handle(self, recipes, warehouse, stock_date, user, dry_run, **options):
        for path in (recipes, warehouse):
            if not path.is_file():
                raise CommandError(f"File not found: {path}")
        stock_date = parse_date(stock_date) if stock_date else datetime.date.today()
        if stock_date is None:
            raise CommandError("--stock-date must be YYYY-MM-DD.")
        user_obj = None
        if user:
            try:
                user_obj = get_user_model().objects.get(username=user)
            except get_user_model().DoesNotExist as error:
                raise CommandError(f"No user {user!r}.") from error

        try:
            books = read_workbooks(recipes, warehouse)
            report = import_workbooks(books, stock_date, source=f"{recipes.name}/Сбор", user=user_obj, dry_run=dry_run)
        except SheetError as error:
            raise CommandError(str(error)) from error

        if report.unmatched:
            raise CommandError(
                "Recipe materials not found in the stock list; add them to ALIASES in production/importer.py:\n  "
                + "\n  ".join(report.unmatched)
            )
        self.stdout.write(
            f"Materials: {report.materials} in the list, {report.new_materials} new; "
            f"opening stock for {report.opening_balances} as of {stock_date:%d.%m.%Y}."
        )
        for name in report.without_recipe:
            self.stdout.write(self.style.WARNING(f"  {name}: no recipe in Сбор, enter it on the Recipes page."))
        self.stdout.write(
            f"Products: {report.products} in Warehouse, {report.new_products} new; "
            f"recipes created for {report.recipes}."
        )
        self.stdout.write(f"Past production days imported: {report.history_days} (history only, stock not changed).")
        if report.saved:
            self.stdout.write(self.style.SUCCESS("Done."))
        else:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
