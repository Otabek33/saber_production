from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finance_saber.planning.importer import import_workbook


class Command(BaseCommand):
    help = (
        "Import the weekly cash plan (sheet 1-week), the collections plan by region (Sells plan, with each "
        "region's manager) and the USD rates (Курс) from the finance workbook. Safe to re-run: plans are "
        "updated in place."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="The .xlsx workbook, e.g. Situation.xlsx")
        parser.add_argument("--user", help="Username to record as the author of the plans")

    def handle(self, path, user, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        user_obj = None
        if user:
            try:
                user_obj = get_user_model().objects.get(username=user)
            except get_user_model().DoesNotExist as error:
                raise CommandError(f"No user {user!r}.") from error
        report = import_workbook(path, user=user_obj)
        self.stdout.write(
            f"Cash plan figures: {report.cash_plans}; collection plan figures: {report.collection_plans}."
        )
        self.stdout.write(f"Region managers set: {report.managers}; exchange rates: {report.rates}.")
        for name in report.new_regions:
            self.stdout.write(f"  New region: {name}")
        for line in report.unknown:
            self.stdout.write(self.style.WARNING(f"  Not found, skipped: {line}"))
        for line in report.skipped_rates:
            self.stdout.write(self.style.WARNING(f"  Rate skipped as a likely typo: {line}"))
        self.stdout.write(self.style.SUCCESS("Done."))
