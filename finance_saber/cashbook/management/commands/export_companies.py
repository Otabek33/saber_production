from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import translation

from finance_saber.cashbook import company_files
from finance_saber.cashbook.exports import xlsx_bytes


class Command(BaseCommand):
    help = (
        "Write the companies and their bank accounts to an .xlsx file, for load_companies or the Companies "
        "page's upload. With --seed, write the project's file (cashbook/seed/companies.xlsx)."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, nargs="?", help="Where to write the file, e.g. companies.xlsx")
        parser.add_argument("--seed", action="store_true", help="Write the project's file")

    def handle(self, path, seed, **options):
        if seed:
            written = company_files.write_seed_file()
            self.stdout.write(self.style.SUCCESS(f"Written {written}"))
            self.stdout.write("Commit it with the code; it holds real INNs, so keep the repository private.")
            return
        if path is None:
            raise CommandError("Give a path, or --seed for the project's file.")
        with translation.override("ru"):
            path.write_bytes(xlsx_bytes([company_files.companies_sheet()]))
        self.stdout.write(self.style.SUCCESS(f"Companies written to {path}"))
