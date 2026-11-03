from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from finance_saber.sales import services


class Command(BaseCommand):
    help = (
        "Import the yearly sales and production plan (a sheet like 'Plan 2026') from an .xlsx workbook. The same "
        "import is on the Sales plan › Upload page. Sales themselves are shipments: Production › Shipments."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="The .xlsx workbook, e.g. Situation.xlsx")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported, save nothing")

    def handle(self, path, dry_run, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        try:
            report = services.import_plan(services.read_plan(path), dry_run=dry_run)
        except services.SheetError as error:
            raise CommandError(str(error)) from error
        for name in report.unknown_products:
            self.stdout.write(self.style.ERROR(f"  Unknown product: {name}"))
        blocks = ", ".join(f"{kind}: {count} products" for kind, count in report.products.items())
        self.stdout.write(f"Plan {report.year}: {blocks}; {report.values} figures.")
        self.stdout.write(self.style.SUCCESS("Plan saved.") if report.saved else self.style.WARNING("Plan not saved."))
