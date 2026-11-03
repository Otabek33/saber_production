from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from finance_saber.cashbook import company_files
from finance_saber.cashbook.handbooks import HandbookError


class Command(BaseCommand):
    help = (
        "Load companies, their filials and bank accounts from an .xlsx file: SmartUp's «Юридическое лицо» export, "
        "the Companies page's Download Excel or export_companies (the project's file if no path is given). Load the "
        "counterparties first: accounts are found by their statement reference. Safe to re-run: companies are "
        "matched by SmartUp ID, then by INN."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, nargs="?", default=company_files.SEED_PATH)
        parser.add_argument("--dry-run", action="store_true", help="Show what would change, save nothing")

    def handle(self, path, dry_run, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        try:
            report = company_files.load_companies(path, dry_run=dry_run)
        except HandbookError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            f"Companies created: {report.created}, updated: {report.updated}, put under a head company: "
            f"{report.filials}; bank requisites added: {report.requisites}, bank accounts connected: {report.linked}"
        )
        if report.waiting:
            waiting = ", ".join(sorted(report.waiting))
            self.stdout.write(self.style.WARNING(f"Filials waiting for a head company not loaded yet: {waiting}"))
        if report.errors:
            for error in report.errors:
                self.stdout.write(self.style.ERROR(f"  {error}"))
            raise CommandError("Nothing was saved: fix the rows above and run again.")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Loaded."))
