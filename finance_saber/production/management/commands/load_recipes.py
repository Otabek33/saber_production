from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from finance_saber.cashbook.handbooks import HandbookError
from finance_saber.production import recipe_excel


class Command(BaseCommand):
    help = (
        "Load products, materials, recipes and other product names from an .xlsx file written by "
        "export_recipes or the Download Excel button (the project's file if no path is given). "
        "Safe to re-run: records are matched by name."
    )
    kind = "recipes"  # see recipe_excel.SEED_FILES
    only = recipe_excel.SHEET_ORDER  # the sheets read

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, nargs="?", default=recipe_excel.seed_path(self.kind))
        parser.add_argument("--dry-run", action="store_true", help="Show what would change, save nothing")

    def load(self, path, dry_run, **options):
        return recipe_excel.load_recipes(path, dry_run=dry_run, only=self.only)

    def handle(self, path, dry_run, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        try:
            report = self.load(path, dry_run, **options)
        except HandbookError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Sheets: {', '.join(report.sheets)}")
        for label, created, updated, unchanged, removed in report.rows():
            self.stdout.write(f"  {label}: {created} new, {updated} changed, {unchanged} unchanged, {removed} removed")
        if report.stock_kept:
            self.stdout.write(
                self.style.WARNING(f"  Stock not loaded (already has movements): {', '.join(report.stock_kept)}")
            )
        if report.errors:
            for error in report.errors:
                self.stdout.write(self.style.ERROR(f"  {error}"))
            raise CommandError("Nothing was saved: fix the rows above and run again.")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Loaded."))
