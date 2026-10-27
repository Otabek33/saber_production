from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from finance_saber.cashbook.handbooks import HandbookError, load_handbooks


class Command(BaseCommand):
    help = (
        "Load the handbooks (articles, categories, regions, counterparties) from an .xlsx file written by "
        "export_handbooks or the Download Excel buttons. Safe to re-run: existing records are matched by name."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="The .xlsx file, e.g. handbooks.xlsx")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change, save nothing")

    def handle(self, path, dry_run, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        try:
            report = load_handbooks(path, dry_run=dry_run)
        except HandbookError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Sheets: {', '.join(report.sheets)}")
        for label, created, updated, unchanged in report.rows():
            self.stdout.write(f"  {label}: {created} new, {updated} changed, {unchanged} unchanged")
        if report.errors:
            for error in report.errors:
                self.stdout.write(self.style.ERROR(f"  {error}"))
            raise CommandError("Nothing was saved: fix the rows above and run again.")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Handbooks loaded."))
