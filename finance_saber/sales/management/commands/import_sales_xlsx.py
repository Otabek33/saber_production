from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finance_saber.sales import services


class Command(BaseCommand):
    help = (
        "Import sales from a SmartUp export (sheet SmartUp, or the first sheet with its columns) and, with "
        "--plan, the yearly plan (sheet like 'Plan 2026'). Safe to re-run: duplicate sales are skipped. "
        "The same import is on the Daily sales › Upload from Excel page."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="The .xlsx workbook, e.g. Situation.xlsx")
        parser.add_argument("--plan", action="store_true", help="Also import the yearly plan from the same workbook")
        parser.add_argument("--no-sales", action="store_true", help="Skip the sales (e.g. to load only the plan)")
        parser.add_argument("--user", help="Username to record as the author")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported, save nothing")

    def handle(self, path, plan, no_sales, user, dry_run, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        user_obj = None
        if user:
            try:
                user_obj = get_user_model().objects.get(username=user)
            except get_user_model().DoesNotExist as error:
                raise CommandError(f"No user {user!r}.") from error
        try:
            if not no_sales:
                self.import_sales(path, user_obj, dry_run)
            if plan:
                self.import_plan(path, dry_run)
        except services.SheetError as error:
            raise CommandError(str(error)) from error

    def import_sales(self, path, user, dry_run):
        report = services.import_sales(services.read_smartup(path), user=user, dry_run=dry_run)
        for problem in report.errors:
            self.stdout.write(self.style.ERROR(f"  {problem}"))
        for name in report.unknown_products:
            self.stdout.write(self.style.ERROR(f"  Unknown product: {name}"))
        self.stdout.write(
            f"Sales: {report.rows} lines, {report.created} new, {len(report.duplicates)} duplicates skipped, "
            f"{len(report.new_clients)} new clients; {report.quantity} units, {report.amount:,.2f}."
        )
        self.stdout.write(
            self.style.SUCCESS("Sales saved.") if report.saved else self.style.WARNING("Sales not saved.")
        )

    def import_plan(self, path, dry_run):
        report = services.import_plan(services.read_plan(path), dry_run=dry_run)
        for name in report.unknown_products:
            self.stdout.write(self.style.ERROR(f"  Unknown product: {name}"))
        blocks = ", ".join(f"{kind}: {count} products" for kind, count in report.products.items())
        self.stdout.write(f"Plan {report.year}: {blocks}; {report.values} figures.")
        self.stdout.write(self.style.SUCCESS("Plan saved.") if report.saved else self.style.WARNING("Plan not saved."))
